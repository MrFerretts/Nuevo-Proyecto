"""Servicio de análisis con IA (Claude) para contexto inteligente.

Toma las estadísticas calculadas por el motor de análisis y genera
insights contextuales que el modelo Poisson no puede captar:
- Derbis, rivalidades históricas
- Contexto de temporada (descenso, título, clasificación europea)
- Rachas y momentum
- Factores tácticos implícitos en los números
- Ajustes de confianza basados en calidad de datos

Requiere: ANTHROPIC_API_KEY en .env
"""

import logging
import json

import aiohttp

from src.services.analysis_engine import TeamAnalysis, BetSuggestion

logger = logging.getLogger(__name__)

API_URL = "https://api.anthropic.com/v1/messages"


class AIAnalysisService:
    """Genera análisis contextual usando Claude."""

    def __init__(self, api_key: str):
        self.api_key = api_key

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
            return None

        prompt = self._build_prompt(home, away, h2h, probs, suggestions, league_name)

        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                }
                payload = {
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 800,
                    "messages": [{"role": "user", "content": prompt}],
                }

                async with session.post(API_URL, headers=headers, json=payload) as resp:
                    if resp.status != 200:
                        error = await resp.text()
                        logger.error(f"Claude API error {resp.status}: {error[:200]}")
                        return None

                    data = await resp.json()
                    content = data.get("content", [])
                    if content and content[0].get("type") == "text":
                        return content[0]["text"]
                    return None

        except Exception as e:
            logger.error(f"Error en AI analysis: {e}")
            return None

    def _build_prompt(
        self,
        home: TeamAnalysis,
        away: TeamAnalysis,
        h2h: dict,
        probs: dict,
        suggestions: list[BetSuggestion],
        league_name: str,
    ) -> str:
        """Construye el prompt para Claude con todas las estadísticas."""

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
        """Genera un resumen IA de las mejores oportunidades del día.

        Args:
            opportunities: Lista de dicts con 'match', 'league', 'suggestion'
        """
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

        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                }
                payload = {
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 500,
                    "messages": [{"role": "user", "content": prompt}],
                }

                async with session.post(API_URL, headers=headers, json=payload) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    content = data.get("content", [])
                    if content and content[0].get("type") == "text":
                        return content[0]["text"]
                    return None
        except Exception as e:
            logger.error(f"Error en AI tips summary: {e}")
            return None
