"""Servicio de IA — CEREBRO del análisis deportivo.

La IA NO es decorativa. Es el cerebro que:
1. Recibe TODOS los datos (stats, xG, odds, line movement, lesiones, forma)
2. RAZONA sobre el contexto (derby, motivación, momento de temporada, tácticas)
3. Produce AJUSTES ESTRUCTURADOS a las probabilidades del modelo
4. Decide la apuesta final con conviction score

Soporta dos backends:
- Anthropic Claude (premium, mejor razonamiento) — con ANTHROPIC_API_KEY
- Groq Llama 3.3 (gratis, rápido) — con GROQ_API_KEY

Requiere al menos una API key en .env
"""

import json
import logging
import time

import aiohttp

from src.services.analysis_engine import TeamAnalysis, BetSuggestion

logger = logging.getLogger(__name__)

# Caché de análisis IA: evita resultados diferentes para el mismo partido en pocos minutos
# Formato: {"home vs away": {"text": "...", "timestamp": float}}
_analysis_cache: dict[str, dict] = {}
_CACHE_TTL = 900  # 15 minutos

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_MODEL_FALLBACK = "llama-3.1-8b-instant"


ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = "claude-sonnet-4-20250514"

# ══════════════════════════════════════════════════════════════════
# SYSTEM PROMPT — LA PERSONALIDAD DEL CEREBRO IA
# ══════════════════════════════════════════════════════════════════

BRAIN_SYSTEM_PROMPT = """Eres un analista profesional de apuestas deportivas con 15 años de experiencia.
Tu trabajo es analizar partidos de fútbol y encontrar VALUE BETS reales.

TU FILOSOFÍA:
- El mercado (las cuotas) es eficiente en ~95% de los casos. Tu ventaja está en el 5% que el mercado valora mal.
- Los números cuentan la historia principal, pero el CONTEXTO decide el capítulo.
- Más vale NO apostar que apostar mal. Si no ves valor claro, dilo sin miedo.
- Un edge de 3% no vale la pena. Necesitas al menos 5-7% para cubrir la varianza.

LO QUE TÚ APORTAS QUE LOS NÚMEROS NO:
1. MOTIVACIÓN: ¿Equipo jugándose descenso vs equipo sin nada en juego? Enorme diferencia.
2. DERBIS/RIVALIDADES: En un derby los stats históricos valen menos. Impredecible.
3. FATIGA MENTAL: Equipos que vienen de eliminaciones europeas dolorosas rinden peor.
4. CONTEXTO TÁCTICO: Un equipo que juega al contraataque vs uno ultraofensivo cambia todo el modelo.
5. ROTACIONES: Si un equipo tiene Champions el miércoles, ¿va a rotar en liga el fin de semana?
6. PRESIÓN MEDIÁTICA: Equipos bajo escrutinio público a veces colapsan, a veces se motivan.
7. CONDICIONES: Lluvia, viento, césped artificial — afectan estilos de juego.
8. LINE MOVEMENT: Si las cuotas se movieron, ¿POR QUÉ? ¿Lesión tardía? ¿Dinero inteligente? ¿Trampa?

REGLAS ESTRICTAS:
- NUNCA recomiendes apostar solo porque hay edge numérico. El edge debe tener SENTIDO contextual.
- Si el edge es >15%, desconfía. Probablemente hay información que no tienes.
- Si los datos son de baja calidad (sin xG real, sin market anchor), reduce tu confianza.
- Sé HONESTO. Si no confías en la apuesta, dilo claro. Mejor perder una oportunidad que perder dinero.
- Cuando dices "confianza alta", SIGNIFICA ALGO. No infles la confianza para quedar bien."""


class AIAnalysisService:
    """Cerebro IA del análisis deportivo. Razona y decide."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.anthropic_key = None  # Se configura externamente si disponible

    async def brain_analyze(
        self,
        home: TeamAnalysis,
        away: TeamAnalysis,
        h2h: dict,
        probs: dict,
        odds: dict,
        suggestions: list[BetSuggestion],
        league_name: str = "",
        line_movement: list = None,
    ) -> dict | None:
        """CEREBRO IA: Analiza todo el contexto y produce ajustes estructurados.

        Este es el método central. Recibe TODOS los datos disponibles y produce:
        1. Ajustes a las probabilidades (+/- para home/draw/away)
        2. Evaluación de cada value bet (confirma, rechaza, o ajusta)
        3. Conviction score (0-10) de la mejor apuesta
        4. Texto de análisis contextual
        5. Factores ocultos que detectó

        Returns: dict con ajustes o None si falla.
        """
        api_key = self.anthropic_key or self.api_key
        if not api_key:
            return None

        cache_key = f"brain_{home.name}_vs_{away.name}".lower()
        cached = _analysis_cache.get(cache_key)
        if cached and (time.time() - cached["timestamp"]) < _CACHE_TTL:
            return cached.get("brain_data")

        prompt = self._build_brain_prompt(
            home, away, h2h, probs, odds, suggestions, league_name, line_movement
        )

        # Intentar con Claude primero (mejor razonamiento), fallback a Groq
        result = None
        if self.anthropic_key:
            result = await self._call_anthropic(prompt, max_tokens=1200)
            if result:
                logger.info("Brain: usando Claude (Anthropic)")
        if not result and self.api_key:
            result = await self._call_groq(prompt, max_tokens=1200)
            if result:
                logger.info("Brain: usando Groq (Llama)")

        if not result:
            return None

        # Parsear output estructurado
        brain_data = self._parse_brain_output(result)
        if brain_data:
            _analysis_cache[cache_key] = {
                "text": brain_data.get("analysis_text", ""),
                "brain_data": brain_data,
                "timestamp": time.time(),
            }
        return brain_data

    def _build_brain_prompt(
        self, home, away, h2h, probs, odds, suggestions, league_name, line_movement,
    ) -> str:
        """Construye el mega-prompt para el cerebro IA."""

        # Line movement text
        lm_text = "No hay datos de movimiento de línea disponibles."
        if line_movement and len(line_movement) >= 2:
            first = line_movement[0]
            last = line_movement[-1]
            changes = []
            for label, key in [("Local", "home_odds"), ("Empate", "draw_odds"), ("Visitante", "away_odds")]:
                old = first.get(key, 0)
                new = last.get(key, 0)
                if old > 1 and new > 1 and abs(old - new) > 0.05:
                    changes.append(f"  {label}: {old:.2f} → {new:.2f}")
            if changes:
                lm_text = "MOVIMIENTO DE CUOTAS:\n" + "\n".join(changes)

        # Suggestions text
        bets_text = "Ningún value bet detectado por el modelo."
        if suggestions:
            lines = []
            for s in suggestions[:5]:
                lines.append(
                    f"  - {s.pick}: cuota {s.odds:.2f}, prob_estimada={s.estimated_prob:.1%}, "
                    f"edge=+{s.value:.1%}, confianza_modelo={s.confidence}"
                )
            bets_text = "\n".join(lines)

        # Odds del mercado
        odds_text = "No disponibles"
        if odds.get("home", 0) > 1:
            odds_text = f"Local={odds.get('home', 0):.2f} | Empate={odds.get('draw', 0):.2f} | Visitante={odds.get('away', 0):.2f}"
            if odds.get("over25", 0) > 1:
                odds_text += f" | O2.5={odds.get('over25', 0):.2f} | U2.5={odds.get('under25', 0):.2f}"

        h2h_total = h2h.get("home_wins", 0) + h2h.get("away_wins", 0) + h2h.get("draws", 0)

        # Data quality
        data_sources = []
        if probs.get("has_real_xg"):
            data_sources.append("xG Real (Understat)")
        if probs.get("has_market_anchor"):
            data_sources.append("Market Anchor")
        data_quality = ", ".join(data_sources) if data_sources else "Solo modelo Poisson básico"

        return f"""{BRAIN_SYSTEM_PROMPT}

═══════════════════════════════════════
DATOS DEL PARTIDO — ANALIZA TODO ESTO
═══════════════════════════════════════

PARTIDO: {home.name} vs {away.name}
LIGA: {league_name}
CALIDAD DE DATOS: {data_quality}

── LOCAL: {home.name} ──
Forma: {home.form_detail} (score: {home.form_score:.0f}/100) | Racha: {home.streak}
Posición: {home.league_position}° ({home.points} pts) | Récord: {home.wins}W-{home.draws}D-{home.losses}L
Goles: {home.goals_scored_avg:.2f} anotados/partido, {home.goals_conceded_avg:.2f} recibidos/partido
En casa: {home.home_goals_scored_avg:.2f} anotados, {home.home_goals_conceded_avg:.2f} recibidos
xG real: {f"{home.real_xg:.2f} xG, {home.real_xga:.2f} xGA" if home.real_xg > 0 else "No disponible"}
Over 2.5: {home.over25_pct:.0f}% | BTTS: {home.btts_pct:.0f}% | Clean Sheet: {home.clean_sheets_pct:.0f}%
Descanso: {f"{home.rest_days} días" if home.rest_days >= 0 else "Desconocido"}
Lesiones: {len(home.injuries)} ({', '.join(home.injuries[:5]) if home.injuries else 'ninguna reportada'})

── VISITANTE: {away.name} ──
Forma: {away.form_detail} (score: {away.form_score:.0f}/100) | Racha: {away.streak}
Posición: {away.league_position}° ({away.points} pts) | Récord: {away.wins}W-{away.draws}D-{away.losses}L
Goles: {away.goals_scored_avg:.2f} anotados/partido, {away.goals_conceded_avg:.2f} recibidos/partido
Fuera: {away.away_goals_scored_avg:.2f} anotados, {away.away_goals_conceded_avg:.2f} recibidos
xG real: {f"{away.real_xg:.2f} xG, {away.real_xga:.2f} xGA" if away.real_xg > 0 else "No disponible"}
Over 2.5: {away.over25_pct:.0f}% | BTTS: {away.btts_pct:.0f}% | Clean Sheet: {away.clean_sheets_pct:.0f}%
Descanso: {f"{away.rest_days} días" if away.rest_days >= 0 else "Desconocido"}
Lesiones: {len(away.injuries)} ({', '.join(away.injuries[:5]) if away.injuries else 'ninguna reportada'})

── H2H: {h2h_total} partidos ──
{home.name}: {h2h.get("home_wins", 0)}W | Empates: {h2h.get("draws", 0)} | {away.name}: {h2h.get("away_wins", 0)}W
Promedio goles: {h2h.get("avg_goals", 0):.1f} | BTTS: {h2h.get("btts_pct", 0):.0f}%

── PROBABILIDADES DEL MODELO ──
Local: {probs["home_win"]:.1%} | Empate: {probs["draw"]:.1%} | Visitante: {probs["away_win"]:.1%}
xG estimado: {probs.get("home_xg", 0):.2f} - {probs.get("away_xg", 0):.2f}
Over 2.5: {probs.get("over25", 0):.1%} | BTTS: {probs.get("btts", 0):.1%}

── CUOTAS DEL MERCADO ──
{odds_text}

── {lm_text} ──

── VALUE BETS DETECTADOS POR EL MODELO ──
{bets_text}

═══════════════════════════════════════
TU TAREA: Analiza TODO lo anterior y responde con este JSON EXACTO.
═══════════════════════════════════════

Responde ÚNICAMENTE con JSON válido (sin markdown, sin ```):

{{
  "prob_adjustments": {{
    "home_win": 0.02,
    "draw": -0.01,
    "away_win": -0.01,
    "reasoning": "Breve explicación de por qué ajustas las probabilidades"
  }},
  "value_bets_evaluation": [
    {{
      "pick": "nombre del pick",
      "verdict": "CONFIRMAR | RECHAZAR | PRECAUCIÓN",
      "adjusted_confidence": "baja | media | alta | muy_alta",
      "reason": "por qué confirmas o rechazas esta apuesta"
    }}
  ],
  "best_bet": {{
    "pick": "la apuesta en la que más confías (o NINGUNA si no confías en ninguna)",
    "conviction": 7,
    "reason": "explicación concisa de por qué"
  }},
  "hidden_factors": [
    "Factor contextual 1 que los números no capturan",
    "Factor contextual 2"
  ],
  "analysis_text": "2-3 párrafos de análisis contextual profundo. Habla de motivación, contexto de temporada, matchup táctico, y cualquier factor que cambie la lectura de los números. Sé directo y honesto. Si no hay valor, dilo sin miedo.",
  "risk_warnings": [
    "Advertencia de riesgo si aplica"
  ]
}}

REGLAS PARA LOS AJUSTES:
- prob_adjustments deben sumar 0 (si subes una, baja otra)
- Ajustes máximos: ±0.05 (5%). Si crees que el modelo está MUY mal, máximo ±0.08
- conviction es 0-10. Solo pon 8+ si estás MUY seguro. La mayoría deben ser 4-7.
- Si no hay NINGUNA apuesta con valor real, pon best_bet.pick = "NINGUNA"
- Sé conservador. Mejor dejar pasar una oportunidad que recomendar mal."""

    def _parse_brain_output(self, text: str) -> dict | None:
        """Parsea la respuesta JSON del cerebro IA."""
        try:
            cleaned = text.strip()
            # Limpiar posible markdown
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[-1]
            if cleaned.endswith("```"):
                cleaned = cleaned.rsplit("```", 1)[0]
            cleaned = cleaned.strip()

            data = json.loads(cleaned)

            # Validar estructura mínima
            if "prob_adjustments" not in data:
                logger.warning("Brain: respuesta sin prob_adjustments")
                return None
            if "best_bet" not in data:
                logger.warning("Brain: respuesta sin best_bet")
                return None

            # Validar que adjustments suman ~0
            adj = data["prob_adjustments"]
            total_adj = adj.get("home_win", 0) + adj.get("draw", 0) + adj.get("away_win", 0)
            if abs(total_adj) > 0.02:
                logger.warning(f"Brain: adjustments no suman 0 (sum={total_adj:.3f}), normalizando")
                # Normalizar
                for key in ["home_win", "draw", "away_win"]:
                    adj[key] = adj.get(key, 0) - total_adj / 3

            # Clamp adjustments
            for key in ["home_win", "draw", "away_win"]:
                adj[key] = max(-0.08, min(0.08, adj.get(key, 0)))

            # Clamp conviction
            if "best_bet" in data:
                data["best_bet"]["conviction"] = max(0, min(10, data["best_bet"].get("conviction", 5)))

            return data

        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Brain: JSON inválido: {text[:300]} - {e}")
            return None

    async def _call_anthropic(self, prompt: str, max_tokens: int = 1200) -> str | None:
        """Llamada a la API de Anthropic (Claude)."""
        if not self.anthropic_key:
            return None

        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    "x-api-key": self.anthropic_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                }
                payload = {
                    "model": ANTHROPIC_MODEL,
                    "max_tokens": max_tokens,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                }

                async with session.post(
                    ANTHROPIC_API_URL, headers=headers, json=payload,
                    timeout=aiohttp.ClientTimeout(total=45),
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        logger.error(f"Anthropic API error {resp.status}: {body[:300]}")
                        return None
                    data = json.loads(await resp.text())
                    content = data.get("content", [])
                    if content:
                        return content[0].get("text", "")
        except Exception as e:
            logger.error(f"Anthropic API error: {e}")
        return None

    async def _call_groq(self, prompt: str, max_tokens: int = 800) -> str | None:
        """Hace una llamada a la API de Groq (compatible con OpenAI).

        Intenta con el modelo principal, si falla intenta con el fallback.
        """
        for model in [GROQ_MODEL, GROQ_MODEL_FALLBACK]:
            try:
                async with aiohttp.ClientSession() as session:
                    headers = {
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    }
                    payload = {
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": max_tokens,
                        "temperature": 0.3,
                    }

                    logger.info(f"Groq: llamando modelo {model}...")
                    async with session.post(GROQ_API_URL, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                        body = await resp.text()
                        if resp.status == 401:
                            logger.error(f"Groq: API KEY INVÁLIDA (401). Verifica GROQ_API_KEY en .env")
                            return None
                        if resp.status == 429:
                            logger.warning(f"Groq: Rate limit alcanzado (429). Intentando modelo fallback...")
                            continue
                        if resp.status != 200:
                            logger.error(f"Groq API error {resp.status} con {model}: {body[:300]}")
                            continue

                        data = json.loads(body)
                        choices = data.get("choices", [])
                        if choices:
                            text = choices[0].get("message", {}).get("content")
                            if text:
                                logger.info(f"Groq: respuesta OK de {model} ({len(text)} chars)")
                                return text

                        logger.warning(f"Groq: respuesta vacía de {model}: {body[:200]}")
                        continue

            except aiohttp.ClientError as e:
                logger.error(f"Groq: error de conexión con {model}: {e}")
                continue
            except Exception as e:
                logger.error(f"Groq: error inesperado con {model}: {e}", exc_info=True)
                continue

        logger.error("Groq: todos los modelos fallaron")
        return None

    async def _call_groq_fast(self, prompt: str, max_tokens: int = 100) -> str | None:
        """Llamada rápida a Groq usando solo el modelo 8B (para clasificación)."""
        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                }
                payload = {
                    "model": GROQ_MODEL_FALLBACK,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                    "temperature": 0,
                }
                async with session.post(
                    GROQ_API_URL, headers=headers, json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        return None
                    data = json.loads(await resp.text())
                    choices = data.get("choices", [])
                    if choices:
                        return choices[0].get("message", {}).get("content")
        except Exception as e:
            logger.error(f"Groq fast: error: {e}")
        return None

    async def classify_intent(self, user_message: str) -> dict | None:
        """Clasifica la intención del usuario con el modelo 8B rápido.

        Returns: {"intent": "bet", "params": "50 al barca over 1.5 x3"} o None.
        """
        if not self.api_key:
            return None

        prompt = f"""Clasifica la intención del usuario. Responde SOLO con JSON válido (sin markdown).

Intenciones:
- "matches": quiere ver la lista de próximos partidos de una liga o en general ("partidos de la premier", "qué partidos hay mañana", "calendario de la liga", "próximos partidos champions", "qué juegos hay hoy")
- "analyze": quiere analizar un partido o equipo ESPECÍFICO en profundidad ("analiza el barca vs madrid", "cómo ves el liverpool vs arsenal", "análisis del madrid mañana")
- "bet": quiere registrar UNA apuesta ("le meto 50 al man u", "apuesto 30 al over 2.5")
- "parlay": quiere una combinada de 2+ picks ("combinada barca + liverpool", "parlay man u y btts juve")
- "resolve": reporta resultado de apuesta ("gané la del barca", "perdí el over", "acerté todas")
- "bankroll": quiere ver su bankroll/plata ("cómo va mi bankroll", "cuánto tengo")
- "stats": quiere estadísticas o rendimiento ("mis stats", "cómo voy", "mi rendimiento", "racha")
- "my_bets": ver apuestas pendientes o historial ("mis apuestas", "qué tengo pendiente")
- "opportunities": buscar qué apostar hoy ("qué hay para hoy", "mejores apuestas", "oportunidades")
- "help": ayuda o preguntas sobre el bot ("ayuda", "qué puedes hacer")
- "chat": conversación general, saludos, opiniones ("hola", "qué opinas de messi", "crees que el barca gana la liga")

Mensaje: "{user_message}"

{{"intent": "...", "params": "..."}}"""

        result = await self._call_groq_fast(prompt, max_tokens=100)
        if not result:
            return None
        return self._clean_json(result)

    async def generate_ai_analysis(
        self,
        home: TeamAnalysis,
        away: TeamAnalysis,
        h2h: dict,
        probs: dict,
        suggestions: list[BetSuggestion],
        league_name: str = "",
        odds: dict = None,
        line_movement: list = None,
    ) -> tuple[str, dict | None]:
        """Genera análisis IA completo — usa brain_analyze si es posible.

        Returns: (texto_para_telegram, brain_data_o_None)
        """
        api_key = self.anthropic_key or self.api_key
        if not api_key:
            return None, None

        # Intentar análisis cerebro completo
        brain_data = await self.brain_analyze(
            home, away, h2h, probs, odds or {}, suggestions,
            league_name, line_movement,
        )

        if brain_data:
            return self._format_brain_report(brain_data, suggestions), brain_data

        # Fallback: análisis legacy si el brain falla
        cache_key = f"{home.name} vs {away.name}".lower()
        cached = _analysis_cache.get(cache_key)
        if cached and (time.time() - cached["timestamp"]) < _CACHE_TTL:
            return cached["text"], None

        prompt = self._build_legacy_prompt(home, away, h2h, probs, suggestions, league_name)
        result = await self._call_groq(prompt, max_tokens=800)
        if result:
            _analysis_cache[cache_key] = {"text": result, "timestamp": time.time()}
        return result, None

    def _format_brain_report(self, brain: dict, suggestions: list) -> str:
        """Formatea la salida del cerebro IA para Telegram."""
        lines = []

        # Análisis contextual
        analysis = brain.get("analysis_text", "")
        if analysis:
            lines.extend(["🧠 *ANÁLISIS IA*", "", analysis, ""])

        # Factores ocultos
        hidden = brain.get("hidden_factors", [])
        if hidden:
            lines.append("⚡ *FACTORES OCULTOS*")
            for f in hidden[:4]:
                lines.append(f"• {f}")
            lines.append("")

        # Evaluación de value bets
        evals = brain.get("value_bets_evaluation", [])
        if evals:
            lines.append("🔍 *EVALUACIÓN IA DE VALUE BETS*")
            for ev in evals:
                verdict = ev.get("verdict", "")
                pick = ev.get("pick", "")
                conf = ev.get("adjusted_confidence", "")
                reason = ev.get("reason", "")
                if verdict == "CONFIRMAR":
                    emoji = "✅"
                elif verdict == "RECHAZAR":
                    emoji = "🚫"
                else:
                    emoji = "⚠️"
                lines.append(f"{emoji} *{pick}*: {verdict} ({conf})")
                if reason:
                    lines.append(f"   _{reason}_")
            lines.append("")

        # Best bet con conviction
        best = brain.get("best_bet", {})
        conviction = best.get("conviction", 0)
        best_pick = best.get("pick", "NINGUNA")
        best_reason = best.get("reason", "")

        conv_bar = "🟩" * min(conviction, 10) + "⬜" * max(0, 10 - conviction)
        lines.extend([
            "🎯 *VEREDICTO FINAL IA*",
            f"Apuesta: *{best_pick}*",
            f"Convicción: {conv_bar} *{conviction}/10*",
        ])
        if best_reason:
            lines.append(f"_{best_reason}_")

        # Warnings
        warnings = brain.get("risk_warnings", [])
        if warnings:
            lines.append("")
            for w in warnings:
                lines.append(f"⚠️ {w}")

        return "\n".join(lines)

    def _build_legacy_prompt(self, home, away, h2h, probs, suggestions, league_name) -> str:
        """Prompt legacy (fallback si brain_analyze falla)."""
        bets_text = ""
        if suggestions:
            bets_lines = [
                f"- {s.pick}: cuota {s.odds:.2f}, edge +{s.value:.1%}"
                for s in suggestions[:5]
            ]
            bets_text = "\n".join(bets_lines)
        else:
            bets_text = "No se detectaron value bets."

        return f"""Eres un analista experto de apuestas deportivas. Analiza este partido brevemente.

PARTIDO: {home.name} vs {away.name} ({league_name})
Forma: {home.form_detail} vs {away.form_detail}
Posición: {home.league_position}° vs {away.league_position}°
Probs: Local {probs['home_win']:.0%} | Empate {probs['draw']:.0%} | Visitante {probs['away_win']:.0%}
xG: {probs.get('home_xg', 0):.2f} - {probs.get('away_xg', 0):.2f}
Value bets: {bets_text}

Responde en español (max 500 chars):
🧠 *ANÁLISIS IA*
[Contexto + factores clave + veredicto en 1 línea]"""

    async def generate_ai_tips_summary(
        self,
        opportunities: list[dict],
    ) -> str | None:
        """Genera un resumen IA de las mejores oportunidades del día."""
        if not self.api_key or not opportunities:
            return None

        opps_text = []
        for i, opp in enumerate(opportunities[:8], 1):
            s = opp["suggestion"]
            opps_text.append(
                f"{i}. {opp['match']} ({opp['league']}): {s.pick} "
                f"@ {s.odds:.2f}, edge +{s.value:.1%}"
            )

        prompt = f"""Eres un analista de apuestas deportivas. Te doy las mejores oportunidades detectadas hoy por mi modelo estadístico (Poisson + value bets).

OPORTUNIDADES:
{chr(10).join(opps_text)}

Responde en español con este formato (máximo 500 caracteres):

🏆 *RESUMEN IA DEL DÍA*

[Selecciona las 2-3 mejores apuestas y explica brevemente por qué confías en ellas. Advierte sobre las que tengan riesgo alto.]

💡 *CONSEJO DEL DÍA*
[1 línea de consejo de bankroll management relevante]

Sé breve y directo. No repitas los datos, solo da tu veredicto."""

        return await self._call_groq(prompt, max_tokens=500)

    async def chat(self, user_message: str, last_analysis: str = "") -> str | None:
        """Responde una pregunta libre del usuario sobre fútbol/apuestas."""
        if not self.api_key:
            return None

        context_block = ""
        if last_analysis:
            # Limitar a 2000 chars para no saturar el prompt
            trimmed = last_analysis[:2000]
            context_block = f"""
CONTEXTO - Último análisis generado por el bot:
{trimmed}
{"[...recortado]" if len(last_analysis) > 2000 else ""}

Usa este análisis como referencia si el usuario pregunta sobre el partido, las probabilidades, value bets o cualquier dato del análisis.
"""

        prompt = f"""Eres un asistente experto en fútbol y apuestas deportivas integrado en un bot de Telegram.
Responde en español, de forma concisa y útil. Si te preguntan sobre un partido,
da tu opinión basada en tu conocimiento y en el contexto disponible.
Si no sabes algo, dilo honestamente.
Máximo 1000 caracteres en tu respuesta. Usa emojis con moderación.
{context_block}
Pregunta del usuario: {user_message}"""

        return await self._call_groq(prompt, max_tokens=600)

    async def parse_bet(self, user_message: str, last_analysis: str = "") -> dict | None:
        """Parsea lenguaje natural de una apuesta y extrae datos estructurados.

        Ejemplo: "le meto 50 al man u over 1.5, paga x3"
        → {"match": "Manchester United vs ...", "pick": "Over 1.5 goles", "odds": 3.0, "stake": 50}
        """
        if not self.api_key:
            return None

        context_block = ""
        if last_analysis:
            trimmed = last_analysis[:1500]
            context_block = f"""
CONTEXTO - Último análisis del bot (usa esto para identificar equipos y partidos):
{trimmed}
"""

        prompt = f"""Eres un parser de apuestas deportivas. El usuario describe una apuesta en lenguaje informal/natural.
Tu trabajo es extraer los datos estructurados.

REGLAS:
- Identifica el PARTIDO (equipos involucrados). Si dice "man u", "barca", "juve", etc., usa el nombre completo.
- Identifica el PICK (qué apuesta: victoria, over/under, BTTS, handicap, etc.)
- Identifica la CUOTA (odds). Puede decir "paga x3", "a 2.10", "cuota 1.85", "@1.90", etc. Si dice "x3" = cuota 3.00
- Identifica el STAKE (monto a apostar). Puede decir "le meto 50", "apuesto 100", "$25", etc.
- Si falta algún dato, pon null en ese campo.
{context_block}
MENSAJE DEL USUARIO: {user_message}

Responde ÚNICAMENTE con un JSON válido (sin markdown, sin ```), con esta estructura exacta:
{{"match": "Equipo A vs Equipo B", "pick": "descripción de la apuesta", "odds": 3.0, "stake": 50}}

Si no puedes identificar al menos el pick, responde: {{"error": "No entendí tu apuesta"}}"""

        result = await self._call_groq(prompt, max_tokens=200)
        if not result:
            return None

        try:
            # Limpiar posibles marcadores de código
            cleaned = result.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[-1]
            if cleaned.endswith("```"):
                cleaned = cleaned.rsplit("```", 1)[0]
            cleaned = cleaned.strip()

            return json.loads(cleaned)
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Groq parse_bet: JSON inválido: {result[:200]} - {e}")
            return None

    def _clean_json(self, text: str) -> dict | None:
        """Limpia y parsea JSON de respuestas de Groq."""
        try:
            cleaned = text.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[-1]
            if cleaned.endswith("```"):
                cleaned = cleaned.rsplit("```", 1)[0]
            return json.loads(cleaned.strip())
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Groq: JSON inválido: {text[:200]} - {e}")
            return None

    async def parse_result(self, user_message: str, pending_bets: list[dict]) -> dict | None:
        """Parsea el resultado de una apuesta desde lenguaje natural.

        Ejemplo: "gané la del barca" → {"bet_id": 3, "result": "win"}
        """
        if not self.api_key or not pending_bets:
            return None

        bets_text = "\n".join(
            f"- ID #{b['id']}: {b['match_name']} | {b['pick']} @ {b['odds']:.2f} | ${b['stake']:.2f}"
            for b in pending_bets
        )

        prompt = f"""Eres un parser. El usuario describe el resultado de una apuesta en lenguaje natural.
Tienes estas apuestas PENDIENTES del usuario:

{bets_text}

REGLAS:
- Identifica a cuál apuesta se refiere el usuario (por equipo, tipo de apuesta, etc.)
- Identifica el resultado: "gané", "acerté", "entró", "sí cayó" = win | "perdí", "no entró", "falló" = loss | "suspendido", "cancelado", "void" = void
- Si dice "todas" o "las dos", devuelve una lista con múltiples resultados.

MENSAJE DEL USUARIO: {user_message}

Responde ÚNICAMENTE con JSON válido (sin markdown, sin ```):
Para una apuesta: {{"bet_id": 3, "result": "win"}}
Para varias: {{"bets": [{{"bet_id": 3, "result": "win"}}, {{"bet_id": 5, "result": "loss"}}]}}
Si no puedes identificar la apuesta: {{"error": "No identifiqué la apuesta"}}"""

        result = await self._call_groq(prompt, max_tokens=200)
        if not result:
            return None
        return self._clean_json(result)

    async def parse_parlay(self, user_message: str, last_analysis: str = "") -> dict | None:
        """Parsea una apuesta combinada/parlay desde lenguaje natural.

        Ejemplo: "barca gana x1.85 + liverpool over 2.5 x2.10, le meto 50"
        → {"legs": [{"match": "...", "pick": "...", "odds": 1.85}, ...], "stake": 50}
        """
        if not self.api_key:
            return None

        context_block = ""
        if last_analysis:
            trimmed = last_analysis[:1000]
            context_block = f"\nCONTEXTO del último análisis:\n{trimmed}\n"

        prompt = f"""Eres un parser de apuestas combinadas (parlays). El usuario describe varias apuestas en una combinada.

REGLAS:
- Identifica CADA pata/leg de la combinada (partido, pick, cuota individual)
- Si dice "man u", "barca", "juve", usa el nombre completo
- Cuotas: "x1.85", "a 2.10", "@1.90", "paga x3" = cuota decimal
- Stake: "le meto 50", "$30", "apuesto 25"
- Separadores comunes: "+", "y", ",", "con"
{context_block}
MENSAJE: {user_message}

Responde ÚNICAMENTE con JSON válido (sin markdown, sin ```):
{{"legs": [{{"match": "Equipo A vs Equipo B", "pick": "Victoria Local", "odds": 1.85}}, {{"match": "Equipo C vs Equipo D", "pick": "Over 2.5", "odds": 2.10}}], "stake": 50}}

Si falta el stake pon null. Si no entiendes: {{"error": "No entendí la combinada"}}"""

        result = await self._call_groq(prompt, max_tokens=400)
        if not result:
            return None
        return self._clean_json(result)

    async def parse_match_query(self, user_message: str, available_matches: list[dict]) -> dict | None:
        """Identifica qué partido quiere analizar el usuario.

        Ejemplo: "el del barca" → {"match_index": 2, "league_id": 140}
        """
        if not self.api_key or not available_matches:
            return None

        matches_text = "\n".join(
            f"- índice {m['index']}: {m['home']} vs {m['away']} (liga_id: {m['league_id']}, {m['league_name']})"
            for m in available_matches
        )

        prompt = f"""Eres un parser. El usuario quiere analizar un partido de fútbol.
Estos son los próximos partidos disponibles:

{matches_text}

REGLAS:
- Identifica el partido al que se refiere el usuario. Puede usar abreviaciones como "barca", "man u", "juve", "liverpool", etc.
- Si menciona un equipo, busca en qué partido juega ese equipo.
- Si menciona dos equipos, busca el partido entre ellos.

MENSAJE DEL USUARIO: {user_message}

Responde ÚNICAMENTE con JSON válido (sin markdown, sin ```):
{{"match_index": 2, "league_id": 140}}
Si no encuentras el partido: {{"error": "No encontré ese partido"}}"""

        result = await self._call_groq(prompt, max_tokens=100)
        if not result:
            return None
        return self._clean_json(result)
