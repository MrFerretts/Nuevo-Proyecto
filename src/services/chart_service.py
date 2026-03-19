"""Genera gráficas de rendimiento para el bankroll tracker."""

import io
import logging
from datetime import datetime

import matplotlib
matplotlib.use("Agg")  # No GUI backend
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

logger = logging.getLogger(__name__)

# Estilo visual
COLORS = {
    "green": "#00C853",
    "red": "#FF1744",
    "blue": "#2979FF",
    "gold": "#FFD600",
    "bg": "#1a1a2e",
    "text": "#e0e0e0",
    "grid": "#333355",
}


def _apply_style(fig, ax):
    """Aplica estilo dark theme consistente."""
    fig.patch.set_facecolor(COLORS["bg"])
    ax.set_facecolor(COLORS["bg"])
    ax.tick_params(colors=COLORS["text"], labelsize=9)
    ax.xaxis.label.set_color(COLORS["text"])
    ax.yaxis.label.set_color(COLORS["text"])
    ax.title.set_color(COLORS["text"])
    ax.grid(True, alpha=0.3, color=COLORS["grid"])
    for spine in ax.spines.values():
        spine.set_color(COLORS["grid"])


def _fig_to_bytes(fig) -> bytes:
    """Convierte una figura matplotlib a bytes PNG."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor(), edgecolor="none")
    buf.seek(0)
    plt.close(fig)
    return buf.read()


def generate_pnl_chart(bets: list, initial_bankroll: float) -> bytes | None:
    """Genera gráfica de evolución del bankroll (P&L acumulado).

    Args:
        bets: lista de apuestas resueltas en orden cronológico
        initial_bankroll: bankroll inicial

    Returns: bytes PNG o None si no hay datos
    """
    if not bets:
        return None

    # Calcular evolución del bankroll
    dates = []
    values = [initial_bankroll]
    cumulative = initial_bankroll

    for bet in bets:
        resolved = bet.get("resolved_at", "")
        if not resolved:
            continue
        try:
            dt = datetime.fromisoformat(resolved.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            dt = datetime.now()

        profit = bet.get("profit", 0) or 0
        result = bet.get("result", "")

        if result == "win":
            cumulative += bet["stake"] * (bet["odds"] - 1)
        elif result == "loss":
            pass  # stake ya fue restado al registrar
        elif result == "void":
            cumulative += bet["stake"]  # devuelto

        dates.append(dt)
        values.append(cumulative)

    if len(dates) < 2:
        return None

    fig, ax = plt.subplots(figsize=(10, 5))
    _apply_style(fig, ax)

    # Color según profit
    final_pnl = values[-1] - initial_bankroll
    line_color = COLORS["green"] if final_pnl >= 0 else COLORS["red"]

    ax.plot(dates, values[1:], color=line_color, linewidth=2.5, zorder=3)
    ax.fill_between(dates, initial_bankroll, values[1:],
                    where=[v >= initial_bankroll for v in values[1:]],
                    color=COLORS["green"], alpha=0.15)
    ax.fill_between(dates, initial_bankroll, values[1:],
                    where=[v < initial_bankroll for v in values[1:]],
                    color=COLORS["red"], alpha=0.15)

    # Línea de bankroll inicial
    ax.axhline(y=initial_bankroll, color=COLORS["gold"], linestyle="--",
               linewidth=1, alpha=0.7, label=f"Inicio: ${initial_bankroll:.0f}")

    pnl_sign = "+" if final_pnl >= 0 else ""
    ax.set_title(f"Evolución del Bankroll  |  {pnl_sign}${final_pnl:.2f}",
                 fontsize=14, fontweight="bold", pad=15)
    ax.set_ylabel("Bankroll ($)", fontsize=11)
    ax.legend(loc="upper left", facecolor=COLORS["bg"], edgecolor=COLORS["grid"],
              labelcolor=COLORS["text"])

    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m"))
    fig.autofmt_xdate()

    return _fig_to_bytes(fig)


def generate_weekly_chart(weekly_stats: list) -> bytes | None:
    """Genera gráfica de P&L semanal (barras).

    Args:
        weekly_stats: lista de dicts con {week, bets, wins, profit, staked}

    Returns: bytes PNG o None
    """
    if not weekly_stats or len(weekly_stats) < 1:
        return None

    weeks = [w["week"] for w in weekly_stats]
    profits = [w["profit"] or 0 for w in weekly_stats]
    colors = [COLORS["green"] if p >= 0 else COLORS["red"] for p in profits]

    fig, ax = plt.subplots(figsize=(10, 5))
    _apply_style(fig, ax)

    bars = ax.bar(range(len(weeks)), profits, color=colors, width=0.6, zorder=3)

    # Labels en cada barra
    for bar, profit in zip(bars, profits):
        y = bar.get_height()
        sign = "+" if profit >= 0 else ""
        ax.text(bar.get_x() + bar.get_width() / 2., y,
                f"{sign}${profit:.0f}", ha="center",
                va="bottom" if profit >= 0 else "top",
                color=COLORS["text"], fontsize=9, fontweight="bold")

    ax.set_xticks(range(len(weeks)))
    ax.set_xticklabels(weeks, rotation=45, ha="right", fontsize=8)
    ax.axhline(y=0, color=COLORS["gold"], linewidth=1, alpha=0.5)

    total_pnl = sum(profits)
    sign = "+" if total_pnl >= 0 else ""
    ax.set_title(f"P&L Semanal  |  Total: {sign}${total_pnl:.2f}",
                 fontsize=14, fontweight="bold", pad=15)
    ax.set_ylabel("Profit ($)", fontsize=11)

    return _fig_to_bytes(fig)


def generate_pick_stats_chart(pick_stats: list) -> bytes | None:
    """Genera gráfica de rendimiento por tipo de apuesta.

    Args:
        pick_stats: lista de dicts con {pick, total, wins, losses, profit, staked}

    Returns: bytes PNG o None
    """
    if not pick_stats:
        return None

    # Tomar top 8 por volumen
    stats = sorted(pick_stats, key=lambda x: x["total"], reverse=True)[:8]

    picks = [s["pick"][:20] for s in stats]
    profits = [s["profit"] or 0 for s in stats]
    totals = [s["total"] for s in stats]
    wins = [s["wins"] or 0 for s in stats]
    colors = [COLORS["green"] if p >= 0 else COLORS["red"] for p in profits]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))
    _apply_style(fig, ax1)
    _apply_style(fig, ax2)

    # Gráfica 1: Profit por tipo
    bars = ax1.barh(range(len(picks)), profits, color=colors, height=0.6, zorder=3)
    ax1.set_yticks(range(len(picks)))
    ax1.set_yticklabels(picks, fontsize=9)
    ax1.axvline(x=0, color=COLORS["gold"], linewidth=1, alpha=0.5)
    ax1.set_title("Profit por Tipo", fontsize=12, fontweight="bold", pad=10)
    ax1.set_xlabel("Profit ($)", fontsize=10)

    # Gráfica 2: Winrate por tipo
    winrates = [(w / t * 100) if t > 0 else 0 for w, t in zip(wins, totals)]
    wr_colors = [COLORS["green"] if wr >= 50 else COLORS["red"] for wr in winrates]
    ax2.barh(range(len(picks)), winrates, color=wr_colors, height=0.6, zorder=3)
    ax2.set_yticks(range(len(picks)))
    ax2.set_yticklabels(picks, fontsize=9)
    ax2.axvline(x=50, color=COLORS["gold"], linewidth=1, alpha=0.5, linestyle="--")
    ax2.set_title("Winrate por Tipo", fontsize=12, fontweight="bold", pad=10)
    ax2.set_xlabel("Winrate (%)", fontsize=10)
    ax2.set_xlim(0, 100)

    fig.suptitle("Rendimiento por Tipo de Apuesta", fontsize=14,
                 fontweight="bold", color=COLORS["text"], y=1.02)
    fig.tight_layout()

    return _fig_to_bytes(fig)
