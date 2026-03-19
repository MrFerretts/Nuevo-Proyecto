"""Servicio de análisis con IA para contexto inteligente.

Usa Groq (gratis, rapidísimo) con Llama 3 para generar insights
contextuales que el modelo Poisson no puede captar:
- Derbis, rivalidades históricas
- Contexto de temporada (descenso, título, clasificación europea)
- Rachas y momentum
- Factores tácticos implícitos en los números
- Ajustes de confianza basados en calidad de datos

Requiere: GROQ_API_KEY en .env (gratis en https://console.groq.com/)
"""

import json
import logging

import aiohttp

from src.services.analysis_engine import TeamAnalysis, BetSuggestion

logger = logging.getLogger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_MODEL_FALLBACK = "llama-3.1-8b-instant"


class AIAnalysisService:
    """Genera análisis contextual usando Groq (Llama 3)."""

    def __init__(self, api_key: str):
        self.api_key = api_key

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
                        "temperature": 0.7,
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

    async def generate_ai_analysis(
        self,
        home: TeamAnalysis,
        away: TeamAnalysis,
        h2h: dict,
        probs: dict,
        suggestions: list[BetSuggestion],
        league_name: str = "",
    ) -> str | None:
        """Genera un análisis contextual con IA a partir de las estadísticas.

        Returns: Texto con el análisis de IA, o None si falla.
        """
        if not self.api_key:
            logger.warning("Groq: no hay API key configurada, saltando análisis IA")
            return None

        logger.info(f"Groq: generando análisis IA para {home.name} vs {away.name}...")
        prompt = self._build_prompt(home, away, h2h, probs, suggestions, league_name)
        result = await self._call_groq(prompt, max_tokens=800)
        if result:
            logger.info(f"Groq: análisis generado OK ({len(result)} chars)")
        else:
            logger.warning("Groq: no se pudo generar análisis IA")
        return result

    def _build_prompt(
        self,
        home: TeamAnalysis,
        away: TeamAnalysis,
        h2h: dict,
        probs: dict,
        suggestions: list[BetSuggestion],
        league_name: str,
    ) -> str:
        """Construye el prompt con todas las estadísticas."""

        # Resumen de value bets
        bets_text = ""
        if suggestions:
            bets_lines = []
            for s in suggestions[:5]:
                bets_lines.append(
                    f"- {s.pick}: cuota {s.odds:.2f}, prob estimada {s.estimated_prob:.1%}, "
                    f"edge +{s.value:.1%}, confianza {s.confidence}"
                )
            bets_text = "\n".join(bets_lines)
        else:
            bets_text = "No se detectaron value bets."

        h2h_total = h2h.get("home_wins", 0) + h2h.get("away_wins", 0) + h2h.get("draws", 0)

        return f"""Eres un analista experto de apuestas deportivas. Analiza este partido y dame insights que los números solos no revelan.

PARTIDO: {home.name} vs {away.name}
LIGA: {league_name}

ESTADÍSTICAS LOCAL ({home.name}):
- Forma: {home.form_detail} ({home.form_score:.0f}/100)
- Racha: {home.streak}
- Goles a favor: {home.goals_scored_avg:.2f}/partido | En contra: {home.goals_conceded_avg:.2f}/partido
- En casa: {home.home_goals_scored_avg:.2f} a favor, {home.home_goals_conceded_avg:.2f} en contra
- Posición: {home.league_position}° ({home.points} pts)
- Récord: {home.wins}W {home.draws}D {home.losses}L ({home.matches_played} jugados)
- Over 2.5: {home.over25_pct:.0f}% | BTTS: {home.btts_pct:.0f}%
- Lesiones: {len(home.injuries)} jugadores ({', '.join(home.injuries[:3]) if home.injuries else 'ninguna conocida'})

ESTADÍSTICAS VISITANTE ({away.name}):
- Forma: {away.form_detail} ({away.form_score:.0f}/100)
- Racha: {away.streak}
- Goles a favor: {away.goals_scored_avg:.2f}/partido | En contra: {away.goals_conceded_avg:.2f}/partido
- Fuera: {away.away_goals_scored_avg:.2f} a favor, {away.away_goals_conceded_avg:.2f} en contra
- Posición: {away.league_position}° ({away.points} pts)
- Récord: {away.wins}W {away.draws}D {away.losses}L ({away.matches_played} jugados)
- Over 2.5: {away.over25_pct:.0f}% | BTTS: {away.btts_pct:.0f}%
- Lesiones: {len(away.injuries)} jugadores ({', '.join(away.injuries[:3]) if away.injuries else 'ninguna conocida'})

H2H: {h2h_total} partidos ({h2h.get('home_wins', 0)} victorias {home.name}, {h2h.get('draws', 0)} empates, {h2h.get('away_wins', 0)} victorias {away.name})
Promedio goles H2H: {h2h.get('avg_goals', 0):.1f}

PROBABILIDADES POISSON:
- Victoria local: {probs['home_win']:.1%}
- Empate: {probs['draw']:.1%}
- Victoria visitante: {probs['away_win']:.1%}
- xG: {probs.get('home_xg', 0):.2f} - {probs.get('away_xg', 0):.2f}
- Over 2.5: {probs.get('over25', 0):.1%}
- BTTS: {probs.get('btts', 0):.1%}

VALUE BETS DETECTADOS:
{bets_text}

Responde en español con este formato EXACTO (usa emojis, máximo 600 caracteres):

🧠 *ANÁLISIS IA*

[2-3 líneas de contexto: rivalidad, momento de temporada, factores clave que los números no muestran]

⚡ *FACTORES CLAVE*
• [Factor 1 que influye en el resultado]
• [Factor 2]
• [Factor 3]

🎯 *VEREDICTO IA*
[1 línea: tu predicción y en qué apuesta confías más, con nivel de confianza]

IMPORTANTE: Sé directo, no repitas las estadísticas que ya di. Dame SOLO insights nuevos que aporten contexto. Si un value bet tiene edge muy alto (>15%), advierte que puede ser trampa de cuota."""

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
