"""
SLAP Newsletter — Box Score Builder  (Newspaper Edition v2)
"""
import json, argparse
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR        = Path(__file__).resolve().parent
GAME_STATE_PATH   = SCRIPT_DIR.parent / "game_state.json"
EMAIL_PATH        = SCRIPT_DIR.parent / "newsletter_substack.html"
BLOCK_OUTPUT      = SCRIPT_DIR / "box_score_block.html"
STANDALONE_OUTPUT = SCRIPT_DIR / "box_score.html"

# Fixed render width for the box-score IMAGE (box_score.jpg). The standalone HTML
# is consumed only by wkhtmltoimage, never read by a human, so we lock it to a
# phone-friendly width. This makes the output image mobile-sized deterministically,
# independent of wkhtmltoimage smart-width behavior or any media query.
MOBILE_IMG_WIDTH = 400

SANS = "Arial, Helvetica, sans-serif"
MONO = "'Courier New', Courier, monospace"

SPORT_ORDER = ["nba","nhl","mlb","nfl","wnba","ncaafb","ncaamb"]
SPORT_EMOJI = {"nba":"🏀","nhl":"🏒","mlb":"⚾","nfl":"🏈","wnba":"🏀","ncaafb":"🏈","ncaamb":"🏀"}
AL_DIVS = {"al east","al central","al west","american league east","american league central","american league west"}
NL_DIVS = {"nl east","nl central","nl west","national league east","national league central","national league west"}

PAGE_CSS = """
*{box-sizing:border-box;margin:0;padding:0;}
body{font-family:Arial,Helvetica,sans-serif;font-size:13px;color:#000;background:#fff;line-height:1.35;}
.bx-page{max-width:960px;margin:0 auto;padding:20px 16px 40px;}
.bx-masthead{text-align:center;border-top:4px solid #000;border-bottom:1px solid #000;padding:10px 0 8px;margin-bottom:24px;}
.bx-masthead h1{font-family:Georgia,serif;font-size:28px;font-weight:700;letter-spacing:.04em;font-style:italic;}
.bx-masthead .bx-date{font-style:italic;font-size:15px;font-weight:700;margin-top:4px;}
.bx-masthead .bx-caption{font-size:11px;color:#777;margin-top:3px;}
.bx-section-bar{border-top:3px solid #000;border-bottom:1px solid #000;padding:4px 0;margin:28px 0 16px;}
.bx-section-bar span{font-size:12px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;}
.bx-sport-hdr{border-bottom:2px solid #000;padding-bottom:2px;margin-bottom:12px;}
.bx-sport-hdr span{font-size:16px;font-weight:700;}
.bx-two-col{display:flex;gap:28px;align-items:flex-start;margin-bottom:16px;}
.bx-two-col .bx-left{flex:0 0 55%;min-width:0;}
.bx-two-col .bx-right{flex:1;min-width:0;}
.bx-div-label{font-size:12px;font-weight:700;margin:10px 0 2px;}
.bx-div-label:first-child{margin-top:0;}
.bx-std-table{width:100%;border-collapse:collapse;font-size:12px;}
.bx-std-table th{text-align:right;font-weight:700;font-size:11px;border-bottom:1px solid #000;padding:1px 4px;}
.bx-std-table th:first-child{text-align:left;}
.bx-std-table td{text-align:right;padding:1px 4px;border-bottom:1px solid #e8e8e8;}
.bx-std-table td:first-child{text-align:left;white-space:nowrap;}
.bx-std-table tr:last-child td{border-bottom:none;}
.bx-leaders-hdr{font-size:14px;font-weight:700;border-bottom:2px solid #000;padding-bottom:2px;margin-bottom:8px;text-align:center;}
.bx-leaders-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px 20px;}
.bx-leaders-cat{margin-bottom:4px;}
.bx-leaders-cat-hdr{font-size:11px;font-weight:700;border-bottom:1px solid #000;padding-bottom:1px;margin-bottom:2px;display:flex;justify-content:space-between;}
.bx-leaders-row{display:flex;justify-content:space-between;font-size:11px;padding:1px 0;border-bottom:1px solid #e8e8e8;}
.bx-leaders-row:last-child{border-bottom:none;}
.bx-leaders-name{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.bx-leaders-val{font-weight:700;flex-shrink:0;padding-left:6px;}
.bx-games-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:6px;margin-bottom:14px;}
.bx-score-card{border:1px solid #ccc;padding:5px 8px;}
.bx-score-row{display:flex;justify-content:space-between;align-items:center;font-size:13px;line-height:1.5;}
.bx-score-team{flex:1;white-space:nowrap;}
.bx-score-num{font-weight:700;font-family:'Courier New',monospace;}
.bx-score-num.winner{font-size:15px;}
.bx-series-note{font-size:10px;color:#555;margin-top:3px;padding-top:2px;border-top:1px solid #e8e8e8;}
.bx-series-over{color:#000;font-weight:700;}
.bx-linescore-wrap{overflow-x:auto;margin:8px 0;}
.bx-linescore{border-collapse:collapse;font-size:11px;font-family:'Courier New',monospace;min-width:300px;}
.bx-linescore th{background:#000;color:#fff;padding:2px 5px;text-align:center;font-size:10px;font-weight:700;}
.bx-linescore th:first-child{text-align:left;background:#333;}
.bx-linescore td{padding:2px 5px;text-align:center;border-bottom:1px solid #e8e8e8;}
.bx-linescore td:first-child{text-align:left;background:#f0f0f0;font-weight:700;border-right:1px solid #ccc;white-space:nowrap;}
.bx-linescore td.rhe{background:#f0f0f0;border-left:1px solid #ccc;}
.bx-linescore tr.winner td{font-weight:700;}
.bx-box-wrap{overflow-x:auto;margin:4px 0;}
.bx-box-hdr{font-size:11px;font-weight:700;background:#000;color:#fff;padding:2px 6px;letter-spacing:.05em;}
.bx-box-hdr.loser{background:#444;}
.bx-box-table{width:100%;border-collapse:collapse;font-size:11px;font-family:'Courier New',monospace;}
.bx-box-table th{background:#333;color:#fff;padding:1px 4px;text-align:center;font-size:10px;font-weight:700;}
.bx-box-table th:first-child,.bx-box-table th:nth-child(2){text-align:left;}
.bx-box-table td{padding:1px 4px;text-align:right;border-bottom:1px solid #f0f0f0;}
.bx-box-table td:first-child{text-align:left;white-space:nowrap;}
.bx-box-table td:nth-child(2){text-align:center;color:#777;font-size:10px;}
.bx-box-table tr:nth-child(even) td{background:#f8f8f8;}
.bx-pts{font-weight:700;}
.bx-mlb-cols{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:6px;}
.bx-notes{font-size:10px;color:#555;padding:3px 0;border-top:1px solid #e8e8e8;line-height:1.4;}
.bx-bracket-hdr{font-size:11px;font-weight:700;border-bottom:1px solid #000;padding-bottom:2px;margin:12px 0 6px;text-transform:uppercase;letter-spacing:.06em;}
.bx-bracket-rounds{display:flex;gap:20px;flex-wrap:wrap;}
.bx-bracket-round-title{font-size:10px;font-weight:700;color:#777;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px;border-bottom:1px solid #e8e8e8;padding-bottom:2px;}
.bx-series{margin-bottom:8px;min-width:120px;}
.bx-series-team{display:flex;justify-content:space-between;font-size:12px;line-height:1.6;}
.bx-series-team.leader{font-weight:700;}
.bx-series-wins{font-family:'Courier New',monospace;font-size:14px;font-weight:700;padding-left:8px;}
.bx-series-summ{font-size:10px;color:#777;}
.bx-series-summ.done{color:#000;font-weight:700;}
.bx-golf-hdr{background:#000;color:#fff;padding:4px 8px;font-size:13px;font-weight:700;margin-bottom:0;}
.bx-golf-table{width:100%;border-collapse:collapse;font-size:11px;}
.bx-golf-table th{background:#333;color:#fff;padding:2px 5px;text-align:center;font-size:10px;font-weight:700;}
.bx-golf-table th:first-child,.bx-golf-table th:nth-child(2){text-align:left;}
.bx-golf-table td{padding:2px 5px;text-align:center;border-bottom:1px solid #f0f0f0;font-family:'Courier New',monospace;}
.bx-golf-table td:first-child{text-align:left;width:28px;}
.bx-golf-table td:nth-child(2){text-align:left;font-family:Arial,sans-serif;white-space:nowrap;}
.bx-golf-table td.under{font-weight:700;}
.bx-golf-table tr:nth-child(even) td{background:#f8f8f8;}
.bx-tennis-table{width:100%;border-collapse:collapse;font-size:12px;}
.bx-tennis-table td{padding:3px 6px;border-bottom:1px solid #f0f0f0;}
.bx-tennis-table td:last-child{text-align:right;font-family:'Courier New',monospace;color:#555;}
.bx-tennis-table tr:nth-child(even) td{background:#f8f8f8;}
.bx-footer{font-size:10px;color:#aaa;text-align:center;border-top:1px solid #e8e8e8;padding-top:8px;margin-top:20px;}
@media(max-width:680px){
  .bx-two-col{flex-direction:column;gap:16px;}
  .bx-two-col .bx-left,.bx-two-col .bx-right{flex:1 1 100%;}
  .bx-leaders-grid{grid-template-columns:1fr;}
  .bx-games-grid{grid-template-columns:1fr;}
  .bx-mlb-cols{grid-template-columns:1fr;}
  .bx-bracket-rounds{flex-direction:column;gap:10px;}
  .bx-masthead h1{font-size:20px;}
  .bx-masthead .bx-date{font-size:13px;}
}
"""

def _safe_int(v):
    try: return int(str(v).strip())
    except: return 0

def _one_standings_table(teams):
    has_diff = any(t.get("differential","") not in ("","?") for t in teams)
    has_home = any(t.get("home_record","") for t in teams)
    has_l10  = any(t.get("last_ten","") for t in teams)
    has_strk = any(t.get("streak","") for t in teams)
    extra_ths = ""
    if has_diff: extra_ths += "<th>Diff</th>"
    if has_home: extra_ths += "<th>Home</th><th>Away</th>"
    if has_l10:  extra_ths += "<th>L10</th>"
    if has_strk: extra_ths += "<th>Strk</th>"
    hdr = f'<tr><th style="text-align:left;">Team</th><th>W</th><th>L</th><th>Pct</th><th>GB</th>{extra_ths}</tr>'
    rows = ""
    for t in teams:
        gb  = str(t.get("games_behind",""))
        gbd = "" if gb in ("-","0","0.0","") else gb
        diff = str(t.get("differential",""))
        if diff and diff not in ("","?"):
            try:
                d = int(diff)
                diff = f"+{d}" if d > 0 else str(d)
            except: pass
        extra_tds = ""
        if has_diff: extra_tds += f"<td>{diff}</td>"
        if has_home: extra_tds += f'<td>{t.get("home_record","")}</td><td>{t.get("away_record","")}</td>'
        if has_l10:  extra_tds += f'<td>{t.get("last_ten","")}</td>'
        if has_strk: extra_tds += f'<td>{t.get("streak","")}</td>'
        rows += f'<tr><td>{t.get("team","?")}</td><td>{t.get("wins","?")}</td><td>{t.get("losses","?")}</td><td>{t.get("win_pct","?")}</td><td>{gbd}</td>{extra_tds}</tr>'
    return f'<table class="bx-std-table">{hdr}{rows}</table>'

def render_standings_html(standings_data):
    if not standings_data: return ""
    if isinstance(standings_data, list):
        return _one_standings_table(standings_data)
    html = ""
    for div_name, teams in standings_data.items():
        if not teams: continue
        html += f'<div class="bx-div-label">{div_name}</div>' + _one_standings_table(teams)
    return html

def render_mlb_standings_html(standings_data):
    al_html = nl_html = ""
    for div_name, teams in standings_data.items():
        if not teams: continue
        block = f'<div class="bx-div-label">{div_name}</div>' + _one_standings_table(teams)
        if div_name.lower() in AL_DIVS: al_html += block
        elif div_name.lower() in NL_DIVS: nl_html += block
        else: al_html += block
    return al_html, nl_html

_MLB_BAT_ORDER = ["battingAverage","homeRuns","RBIs","onBasePlusSlugging","stolenBases"]
_MLB_PIT_ORDER = ["ERA","wins","strikeouts","WHIP","saves"]
_NBA_ORDER     = ["points","rebounds","assists","steals","blocks"]
_NHL_ORDER     = ["points","goals","assists","goalsAgainstAverage","savePct"]

def _leaders_cat(cat_data):
    label   = cat_data["label"]
    leaders = cat_data["leaders"][:5]
    rows = "".join(
        f'<div class="bx-leaders-row">'
        f'<span class="bx-leaders-name">{i+1}. {p["name"]}'
        f'<span style="color:#777;font-size:10px;"> {p["team"]}</span></span>'
        f'<span class="bx-leaders-val">{p["value"]}</span></div>'
        for i,p in enumerate(leaders)
    )
    return f'<div class="bx-leaders-cat"><div class="bx-leaders-cat-hdr"><span>{label}</span></div>{rows}</div>'

def render_leaders_html(leaders, sport_key):
    if not leaders: return ""
    if sport_key == "mlb":
        order = _MLB_BAT_ORDER + _MLB_PIT_ORDER
    elif sport_key in ("nba","wnba"):
        order = _NBA_ORDER
    elif sport_key == "nhl":
        order = _NHL_ORDER
    else:
        order = list(leaders.keys())
    cats = [leaders[k] for k in order if k in leaders]
    if not cats: return ""
    items = "".join(_leaders_cat(c) for c in cats)
    return f'<div class="bx-leaders-hdr">Leaders</div><div class="bx-leaders-grid">{items}</div>'

def render_linescore_html(away_abbr, home_abbr, period_labels,
                          away_scores, home_scores,
                          away_total=None, home_total=None,
                          away_rhe=None, home_rhe=None):
    is_mlb  = away_rhe is not None
    away_r  = _safe_int(away_rhe.get("R") if is_mlb else away_total)
    home_r  = _safe_int(home_rhe.get("R") if is_mlb else home_total)
    aw_cls  = " winner" if away_r > home_r else ""
    hm_cls  = " winner" if home_r > away_r else ""
    period_ths = "".join(f"<th>{p}</th>" for p in period_labels)
    suffix_ths = "<th>R</th><th>H</th><th>E</th>" if is_mlb else "<th>TOT</th>"
    def _cells(scores, rhe=None, total=None):
        c = "".join(f"<td>{s}</td>" for s in scores)
        if rhe: c += f'<td class="rhe">{rhe.get("R","-")}</td><td class="rhe">{rhe.get("H","-")}</td><td class="rhe">{rhe.get("E","-")}</td>'
        elif total is not None: c += f'<td class="rhe">{total}</td>'
        return c
    return (
        f'<div class="bx-linescore-wrap"><table class="bx-linescore">'
        f'<tr><th></th>{period_ths}{suffix_ths}</tr>'
        f'<tr class="{aw_cls.strip()}"><td>{away_abbr}</td>{_cells(away_scores,away_rhe,away_total)}</tr>'
        f'<tr class="{hm_cls.strip()}"><td>{home_abbr}</td>{_cells(home_scores,home_rhe,home_total)}</tr>'
        f'</table></div>'
    )

def render_score_card(game):
    away = game["away_team"]; home = game["home_team"]
    as_, hs = game["away_score"], game["home_score"]
    winner  = game.get("winner","")
    ot_str  = " OT" if game.get("overtime") else ""
    aw_cls  = "winner" if winner==away else ""
    hm_cls  = "winner" if winner==home else ""
    series  = game.get("series")
    snote   = ""
    if series:
        if series.get("series_over"):
            snote = f'<div class="bx-series-note bx-series-over">SERIES OVER · {series.get("summary","")}</div>'
        else:
            ng = series.get("next_game_number","?")
            snote = f'<div class="bx-series-note">{series.get("summary","")} · Gm {ng}</div>'
    return (
        f'<div class="bx-score-card">'
        f'<div class="bx-score-row"><span class="bx-score-team">{away}</span><span class="bx-score-num {aw_cls}">{as_}</span></div>'
        f'<div class="bx-score-row"><span class="bx-score-team">{home}</span><span class="bx-score-num {hm_cls}">{hs}{ot_str}</span></div>'
        f'{snote}</div>'
    )

NBA_KEYS = ["MIN","FG","3PT","FT","REB","AST","STL","BLK","PTS"]
BAT_KEYS = ["AB", "R", "H", "RBI", "BB", "K", "AVG"]
PIT_KEYS = ["IP", "H", "R", "ER", "BB", "K", "ERA"]

def _nba_player_table(abbr, players, is_winner):
    if not players: return ""
    hdr_cls = "" if is_winner else " loser"
    key_ths = "".join(f"<th>{k}</th>" for k in NBA_KEYS)
    rows = ""
    for p in players:
        s = p.get("stats",{}); dot = "•" if p.get("starter") else " "
        rows += f'<tr><td>{dot} {p["name"]}</td><td>{p.get("pos","")}</td>' + "".join(f'<td class="{"bx-pts" if k=="PTS" else ""}">{s.get(k,"—")}</td>' for k in NBA_KEYS) + "</tr>"
    return f'<div class="bx-box-hdr{hdr_cls}">{abbr}</div><div class="bx-box-wrap"><table class="bx-box-table"><tr><th>Player</th><th></th>{key_ths}</tr>{rows}</table></div>'

def render_nba_game_html(game):
    box = game.get("box_score",{})
    away = game["away_team"]; away_abbr = game.get("away_abbr","")
    home = game["home_team"]; home_abbr = game.get("home_abbr","")
    as_, hs = game["away_score"], game["home_score"]
    winner = game.get("winner",""); ot_str = " (OT)" if game.get("overtime") else ""
    series = game.get("series"); snote = ""
    if series:
        if series.get("series_over"):
            snote = f'<div style="font-size:11px;font-weight:700;background:#000;color:#fff;padding:2px 6px;margin-bottom:4px;">SERIES OVER — {winner} advances · {series.get("summary","")}</div>'
        else:
            snote = f'<div style="font-size:11px;color:#555;padding:2px 0 4px;">{series.get("summary","")} · Game {series.get("next_game_number","?")} next</div>'
    ls = box.get("linescore",{}); ls_html = ""
    if ls.get("period_labels"):
        ls_html = render_linescore_html(away_abbr,home_abbr,ls["period_labels"],ls.get("away_periods",[]),ls.get("home_periods",[]),away_total=str(as_),home_total=str(hs))
    away_side = box.get("away",{}); home_side = box.get("home",{})
    if not away_side.get("players") and not home_side.get("players"):
        for sd in box.values():
            if isinstance(sd,dict) and "players" in sd:
                if sd.get("team")==away_abbr: away_side=sd
                elif sd.get("team")==home_abbr: home_side=sd
    player_html = _nba_player_table(away_abbr,away_side.get("players",[]),winner==away) + _nba_player_table(home_abbr,home_side.get("players",[]),winner==home)
    if not player_html: player_html = '<p style="font-size:11px;color:#777;padding:4px;">Stats not yet available.</p>'
    notes_html = "".join(f'<div class="bx-notes">{n}</div>' for n in box.get("notes",[]))
    headline = f'<div style="font-size:14px;font-weight:700;border-bottom:2px solid #000;padding-bottom:3px;margin-bottom:6px;">{away.upper()} {as_} &nbsp; {home.upper()} {hs}{ot_str}</div>'
    return f'<div style="margin-bottom:20px;">{headline}{snote}{ls_html}{player_html}{notes_html}</div>'

def _mlb_bat_table(abbr, players, is_winner):
    hdr_cls = "" if is_winner else " loser"
    key_ths = "".join(f"<th>{k}</th>" for k in BAT_KEYS)
    rows = "".join(f'<tr><td>{p["name"]} {p.get("pos","")}</td>' + "".join(f'<td class="{"bx-pts" if k in ("H","RBI") else ""}">{p.get("stats",{}).get(k,"—")}</td>' for k in BAT_KEYS) + "</tr>" for p in players)
    return f'<div><div class="bx-box-hdr{hdr_cls}">BATTING — {abbr}</div><div class="bx-box-wrap"><table class="bx-box-table"><tr><th>{abbr}</th>{key_ths}</tr>{rows}</table></div></div>'

def _mlb_pit_table(abbr, pitchers, outcome_note, is_winner):
    hdr_cls = "" if is_winner else " loser"
    key_ths = "".join(f"<th>{k}</th>" for k in PIT_KEYS)
    rows = "".join(f'<tr><td>{p["name"]}{" (" + p["note"] + ")" if p.get("note") else ""}</td>' + "".join(f'<td>{p.get("stats",{}).get(k,"—")}</td>' for k in PIT_KEYS) + "</tr>" for p in pitchers)
    return f'<div><div class="bx-box-hdr{hdr_cls}">PITCHING — {abbr} {outcome_note}</div><div class="bx-box-wrap"><table class="bx-box-table"><tr><th>{abbr}</th>{key_ths}</tr>{rows}</table></div></div>'

def render_mlb_game_html(game):
    box = game.get("box_score",{}); away = game["away_team"]; away_abbr = game.get("away_abbr","")
    home = game["home_team"]; home_abbr = game.get("home_abbr",""); as_, hs = game["away_score"], game["home_score"]; winner = game.get("winner","")
    away_box = box.get("away",{}); home_box = box.get("home",{})
    ls = box.get("linescore",{}); ls_html = ""
    if ls.get("away_innings"):
        num = ls.get("num_innings",9); labels = [str(i) for i in range(1,num+1)]
        away_rhe = dict(ls.get("away_rhe") or {}); home_rhe = dict(ls.get("home_rhe") or {})
        if str(away_rhe.get("R","-"))=="-": away_rhe["R"]=str(as_)
        if str(home_rhe.get("R","-"))=="-": home_rhe["R"]=str(hs)
        ls_html = render_linescore_html(away_abbr,home_abbr,labels,ls.get("away_innings",[]),ls.get("home_innings",[]),away_rhe=away_rhe,home_rhe=home_rhe)
    has_stats = bool(away_box.get("batting") or home_box.get("batting") or away_box.get("pitching") or home_box.get("pitching"))
    win_note = "(W)" if winner==home else "(L)"; los_note = "(L)" if winner==home else "(W)"
    box_html = ""
    if has_stats:
        box_html = (f'<div class="bx-mlb-cols">{_mlb_bat_table(away_abbr,away_box.get("batting",[]),winner==away)}{_mlb_bat_table(home_abbr,home_box.get("batting",[]),winner==home)}</div>'
                    f'<div class="bx-mlb-cols">{_mlb_pit_table(away_abbr,away_box.get("pitching",[]),los_note,winner==away)}{_mlb_pit_table(home_abbr,home_box.get("pitching",[]),win_note,winner==home)}</div>')
    notes_html = "".join(f'<div class="bx-notes">{n}</div>' for n in box.get("notes",[]))
    headline = f'<div style="font-size:14px;font-weight:700;border-bottom:2px solid #000;padding-bottom:3px;margin-bottom:6px;">{away.upper()} {as_} &nbsp; {home.upper()} {hs}</div>'
    return f'<div style="margin-bottom:20px;">{headline}{ls_html}{box_html}{notes_html}</div>'

def render_generic_game_html(game):
    box = game.get("box_score",{}); away = game["away_team"]; away_abbr = game.get("away_abbr","")
    home = game["home_team"]; home_abbr = game.get("home_abbr",""); as_, hs = game["away_score"], game["home_score"]
    winner = game.get("winner",""); ot_str = " (OT)" if game.get("overtime") else ""
    series = game.get("series"); snote = ""
    if series:
        if series.get("series_over"):
            snote = f'<div style="font-size:11px;font-weight:700;background:#000;color:#fff;padding:2px 6px;margin-bottom:4px;">SERIES OVER — {winner} advances</div>'
        else:
            snote = f'<div style="font-size:11px;color:#555;padding:2px 0 4px;">{series.get("summary","")} · Game {series.get("next_game_number","?")} next</div>'
    ls = box.get("linescore",{}); ls_html = ""
    if ls.get("period_labels") and ls.get("away_periods"):
        ls_html = render_linescore_html(away_abbr,home_abbr,ls["period_labels"],ls.get("away_periods",[]),ls.get("home_periods",[]),away_total=str(as_),home_total=str(hs))
    notes_html = "".join(f'<div class="bx-notes">{n}</div>' for n in box.get("notes",[]))
    headline = f'<div style="font-size:14px;font-weight:700;border-bottom:2px solid #000;padding-bottom:3px;margin-bottom:6px;">{away.upper()} {as_} &nbsp; {home.upper()} {hs}{ot_str}</div>'
    return f'<div style="margin-bottom:16px;">{headline}{snote}{ls_html}{notes_html}</div>'

def render_bracket_html(bracket_data):
    if not bracket_data: return ""
    rounds_html = ""
    for rnd in bracket_data:
        title = rnd.get("round_title",""); series = rnd.get("series",[])
        if not series: continue
        s_html = ""
        for s in series:
            a_abbr=s.get("away_abbr","?"); h_abbr=s.get("home_abbr","?")
            a_seed=s.get("away_seed",""); h_seed=s.get("home_seed","")
            a_wins=s.get("away_wins",0); h_wins=s.get("home_wins",0)
            done=s.get("series_over",False); summ=s.get("summary","")
            a_str=f"({a_seed}) {a_abbr}" if a_seed else a_abbr
            h_str=f"({h_seed}) {h_abbr}" if h_seed else h_abbr
            a_cls=" leader" if a_wins>=h_wins else ""; h_cls=" leader" if h_wins>=a_wins else ""
            s_html += (f'<div class="bx-series"><div class="bx-series-team{a_cls}"><span>{a_str}</span><span class="bx-series-wins">{a_wins}</span></div>'
                       f'<div class="bx-series-team{h_cls}"><span>{h_str}</span><span class="bx-series-wins">{h_wins}</span></div>'
                       f'<div class="bx-series-summ{"  done" if done else ""}">{summ}{"  ✓" if done else ""}</div></div>')
        rounds_html += f'<div style="min-width:140px;"><div class="bx-bracket-round-title">{title}</div>{s_html}</div>'
    return f'<div class="bx-bracket-hdr">Playoff Bracket</div><div class="bx-bracket-rounds">{rounds_html}</div>'

def render_golf_html(t):
    name=t.get("tournament_name","Golf"); status=t.get("status",""); rnd=t.get("round",""); players=t.get("players",[])
    if not players: return ""
    def _key(p):
        v=str(p.get("to_par","999")).replace("+","").replace("E","0")
        try: return int(v)
        except: return 999
    players=sorted(players,key=_key)[:30]
    max_rounds=max((len(p.get("rounds",[])) for p in players),default=0)
    round_ths="".join(f"<th>R{i+1}</th>" for i in range(max_rounds))
    rows=""
    for p in players:
        to_par=str(p.get("to_par","")); total=str(p.get("total","")); rnds=p.get("rounds",[])
        par_cls=" under" if to_par.startswith("-") else ""
        round_tds="".join(f'<td>{rnds[j] if j<len(rnds) else ""}</td>' for j in range(max_rounds))
        rows+=f'<tr><td>{p.get("pos","")}</td><td>{p.get("name","?")} <span style="color:#777;font-size:10px;">{p.get("country","")}</span></td>{round_tds}<td>{total or "—"}</td><td class="{par_cls.strip()}">{to_par or "—"}</td></tr>'
    status_str=f" — {status}" if status else ""; rnd_str=f" | {rnd}" if rnd else ""
    return f'<div style="margin-bottom:20px;"><div class="bx-golf-hdr">{name.upper()}{status_str}{rnd_str}</div><div style="overflow-x:auto;"><table class="bx-golf-table"><tr><th>#</th><th>Player</th>{round_ths}<th>Tot</th><th>+/-</th></tr>{rows}</table></div></div>'

def render_tennis_html(t):
    name=t.get("tournament_name","Tennis"); surface=t.get("surface",""); rnd=t.get("round","")
    matches=[m for m in t.get("matches",[]) if m.get("completed")]
    if not matches: return ""
    rows="".join(f'<tr><td><strong>{m.get("winner","?")}</strong></td><td style="color:#777;">def.</td><td>{m.get("loser","?")}</td><td>{m.get("score","")}</td></tr>' for m in matches)
    surface_str=f" · {surface}" if surface else ""; rnd_str=f" — {rnd}" if rnd else ""
    return f'<div style="margin-bottom:16px;"><div class="bx-golf-hdr">{name.upper()}{surface_str}{rnd_str}</div><table class="bx-tennis-table">{rows}</table></div>'

def _render_sport_section(sport_key, sport_data, in_playoffs):
    label=sport_data.get("label",sport_key.upper()); emoji=SPORT_EMOJI.get(sport_key,"🏟")
    games=[g for g in sport_data.get("yesterday_games",[]) if g.get("completed")]
    standings=sport_data.get("standings",{}); bracket=sport_data.get("bracket",[]); leaders=sport_data.get("leaders",{})
    if not games and not standings and not bracket: return ""
    html=f'<div style="margin-bottom:28px;"><div class="bx-sport-hdr"><span>{emoji} {label.upper()}</span></div>'
    if in_playoffs:
        if bracket: html+=render_bracket_html(bracket)
        for game in games:
            if sport_key in ("nba","wnba"): html+=render_nba_game_html(game)
            elif sport_key=="mlb": html+=render_mlb_game_html(game)
            else: html+=render_generic_game_html(game)
    else:
        leaders_html=render_leaders_html(leaders,sport_key); std_html=render_standings_html(standings)
        if std_html or leaders_html:
            left=f'<div class="bx-left">{std_html}</div>' if std_html else ""
            right=f'<div class="bx-right">{leaders_html}</div>' if leaders_html else ""
            html+=f'<div class="bx-two-col">{left}{right}</div>'
        if games:
            score_cards="".join(render_score_card(g) for g in games)
            html+=f'<div class="bx-games-grid">{score_cards}</div>'
    html+="</div>"
    return html

# ── Inline-styled MLB renderer (email-safe) ────────────────────────────────
# All styles are inline so the block renders correctly when --append injects
# it into newsletter_email.html (where PAGE_CSS is absent).
# The two-column layout uses the fluid-hybrid technique (inline-block + max-width)
# so it stacks on mobile without media queries.

_I = "#1a1a1a"; _M = "#5f5f5f"; _H = "#e4e4e4"; _S = "#f5f5f5"
_SANS = "Arial,Helvetica,sans-serif"; _MONO = "'Courier New',Courier,monospace"

_MLB_AL = {"NYY","BOS","TB","TBR","TOR","BAL","CLE","DET","KC","KCR",
           "CWS","CHW","MIN","HOU","LAA","ANA","OAK","ATH","SEA","TEX"}
_MLB_NL = {"ATL","PHI","NYM","WSH","WSN","MIA","CHC","MIL","STL","CIN",
           "PIT","LAD","SD","SDP","SF","SFG","ARI","AZ","COL"}

_MLB_LEAD_LEFT  = [("battingAverage","Batting Average","AVG"),("homeRuns","Home Runs","HR"),
                   ("RBIs","RBI","RBI"),("stolenBases","Stolen Bases","SB")]
_MLB_LEAD_RIGHT = [("wins","Wins","W"),("ERA","ERA","ERA"),("strikeouts","Strikeouts","SO"),
                   ("saves","Saves","SV")]
_MLB_BAT = ["AB","R","H","RBI","BB","K","AVG"]
_MLB_PIT = ["IP","H","R","ER","BB","K","ERA"]

def _mi_rule(label):
    return (f'<div style="border-top:2px solid {_I};border-bottom:1px solid {_I};'
            f'margin:22px 0 10px;padding:3px 0;font-family:{_SANS};font-size:11px;'
            f'font-weight:bold;letter-spacing:.09em;text-transform:uppercase;'
            f'color:{_M};">{label}</div>')

def _mi_half(left, right, lmax=312, rmax=312):
    """Fluid-hybrid two columns: side-by-side desktop, stacked mobile, no media queries."""
    cl = (f'<div style="display:inline-block;vertical-align:top;width:100%;max-width:{lmax}px;'
          f'box-sizing:border-box;padding:0 10px;font-size:13px;line-height:1.4;">')
    cr = (f'<div style="display:inline-block;vertical-align:top;width:100%;max-width:{rmax}px;'
          f'box-sizing:border-box;padding:0 10px;font-size:13px;line-height:1.4;">')
    return (f'<div style="font-size:0;line-height:0;text-align:left;">'
            f'{cl}{left}</div>{cr}{right}</div></div>')

def _mi_std_table(teams, div_label=None):
    """Trimmed standings: Team/W/L/Pct/GB/Strk — fits email/mobile width.
    Optional div_label renders as a colspan header row inside the table,
    guaranteeing pixel-perfect alignment with the Team column."""
    th = (f'padding:1px 4px;border-bottom:1px solid {_I};font-family:{_SANS};font-size:10px;'
          f'font-weight:bold;color:{_M};text-align:right;white-space:nowrap;')
    th0 = th + 'text-align:left;'
    td  = (f'padding:1px 4px;border-bottom:.5px solid {_H};font-family:{_MONO};'
           f'font-size:11px;text-align:right;color:{_I};white-space:nowrap;')
    td0 = (f'padding:1px 4px;border-bottom:.5px solid {_H};font-family:{_SANS};'
           f'font-size:12px;text-align:left;color:{_I};white-space:nowrap;')
    div_row = ""
    if div_label:
        div_row = (f'<tr><td colspan="6" style="padding:10px 4px 2px;font-family:{_SANS};'
                   f'font-size:13px;font-weight:bold;color:{_I};">{div_label}</td></tr>')
    hdr = (f'<tr><th style="{th0}">Team</th><th style="{th}">W</th><th style="{th}">L</th>'
           f'<th style="{th}">Pct</th><th style="{th}">GB</th><th style="{th}">Strk</th></tr>')
    rows = ""
    for t in teams:
        gb = str(t.get("games_behind","")); gb = "" if gb in ("-","0","0.0","") else gb
        rows += (f'<tr><td style="{td0}">{t.get("team","?")}</td>'
                 f'<td style="{td}">{t.get("wins","")}</td><td style="{td}">{t.get("losses","")}</td>'
                 f'<td style="{td}">{t.get("win_pct","")}</td><td style="{td}">{gb}</td>'
                 f'<td style="{td}">{t.get("streak","")}</td></tr>')
    return (f'<table style="border-collapse:collapse;width:100%;margin-bottom:4px;">'
            f'{div_row}{hdr}{rows}</table>')

def _mi_std_half(standings, div_set):
    out = ""
    for name, teams in standings.items():
        if name.lower() not in div_set or not teams:
            continue
        div_label = name.replace("American League ","").replace("National League ","")
        out += _mi_std_table(teams, div_label)
    return out

def _mi_cat(leaders, key, title, abbr, team_set):
    cat = leaders.get(key)
    if not cat: return ""
    picks = [p for p in cat.get("leaders",[]) if p.get("team","") in team_set][:5]
    if not picks: return ""
    rows = "".join(
        f'<div style="display:flex;justify-content:space-between;padding:1px 0;'
        f'border-bottom:.5px solid {_H};font-family:{_SANS};font-size:12px;color:{_I};">'
        f'<span>{i+1}. {p["name"]} <span style="color:#9a9a9a;font-size:10px;">{p["team"]}</span></span>'
        f'<span style="font-family:{_MONO};font-weight:bold;">{p["value"]}</span></div>'
        for i, p in enumerate(picks)
    )
    # Category header: long title on the left, stat abbreviation right-aligned
    # (matches the reference — e.g. "Home Runs ........ HR").
    hdr = (f'<div style="display:flex;justify-content:space-between;align-items:baseline;'
           f'border-bottom:1px solid {_I};padding:4px 0 1px;">'
           f'<span style="font-family:{_SANS};font-size:11px;font-weight:bold;color:{_I};">{title}</span>'
           f'<span style="font-family:{_SANS};font-size:10px;font-weight:bold;color:{_M};">{abbr}</span></div>')
    return f'<div style="margin-bottom:10px;padding:0 10px;">{hdr}{rows}</div>'

def _mi_leaders_half(leaders, team_set, cats):
    return "".join(_mi_cat(leaders, k, title, abbr, team_set) for k, title, abbr in cats) or ""

def _mi_linescore(g):
    ls = g.get("box_score",{}).get("linescore",{})
    ai = ls.get("away_innings")
    if not ai: return ""
    hi = ls.get("home_innings",[]); n = ls.get("num_innings", max(len(ai),9))
    arhe = ls.get("away_rhe",{}); hrhe = ls.get("home_rhe",{})
    aw = g.get("away_abbr","AWY"); hm = g.get("home_abbr","HOM")
    as_ = g.get("away_score",0); hs = g.get("home_score",0)
    # R from game score if RHE is missing
    if str(arhe.get("R","-")) == "-": arhe = dict(arhe); arhe["R"] = str(as_)
    if str(hrhe.get("R","-")) == "-": hrhe = dict(hrhe); hrhe["R"] = str(hs)
    away_win = as_ > hs
    hh = (f'padding:2px 5px;text-align:center;font-family:{_SANS};font-size:10px;'
          f'font-weight:bold;color:{_I};border-bottom:1px solid {_I};')
    hh0 = hh + 'text-align:left;'
    td  = f'padding:2px 5px;text-align:center;font-family:{_MONO};font-size:11px;border-bottom:.5px solid {_H};'
    td0 = f'padding:2px 5px;text-align:left;font-weight:bold;font-family:{_SANS};font-size:11px;white-space:nowrap;border-bottom:.5px solid {_H};'
    rhe_s = f'{td}font-weight:bold;'
    hdr = f'<tr><th style="{hh0}"></th>' + "".join(f'<th style="{hh}">{i+1}</th>' for i in range(n))
    hdr += f'<th style="{hh}">R</th><th style="{hh}">H</th><th style="{hh}">E</th></tr>'
    def _row(abbr, inns, rhe, win):
        w = ";font-weight:bold;" if win else ""
        cells = "".join(f'<td style="{td}{w}">{inns[i] if i < len(inns) else ""}</td>' for i in range(n))
        return (f'<tr><td style="{td0}">{abbr}</td>{cells}'
                f'<td style="{rhe_s}{w}">{rhe.get("R","-")}</td>'
                f'<td style="{rhe_s}{w}">{rhe.get("H","-")}</td>'
                f'<td style="{rhe_s}{w}">{rhe.get("E","-")}</td></tr>')
    body = _row(aw, ai, arhe, away_win) + _row(hm, hi, hrhe, not away_win)
    return (f'<div style="overflow-x:auto;margin:6px 0;">'
            f'<table style="border-collapse:collapse;min-width:320px;">{hdr}{body}</table></div>')

def _mi_stat_table(abbr, players, keys, kind):
    if not players: return ""
    # Newspaper style: white background, black text, ruled headers (no filled blocks).
    th  = (f'padding:2px 4px;text-align:right;font-family:{_SANS};font-size:10px;'
           f'font-weight:bold;color:{_I};border-bottom:1px solid {_I};white-space:nowrap;')
    cap = (f'padding:4px 2px 1px;text-align:left;font-family:{_SANS};font-size:12px;'
           f'font-weight:bold;color:{_I};border-bottom:2px solid {_I};'
           f'letter-spacing:.04em;')
    td  = (f'padding:1px 4px;text-align:right;border-bottom:.5px solid {_H};'
           f'font-family:{_MONO};font-size:11px;color:{_I};')
    td0 = (f'padding:1px 4px;text-align:left;border-bottom:.5px solid {_H};'
           f'font-family:{_SANS};font-size:11px;color:{_I};white-space:nowrap;')
    lbl = "Batter" if kind == "Batting" else "Pitcher"
    head = (f'<tr><td style="{cap}" colspan="{len(keys)+1}">{abbr} &mdash; {kind}</td></tr>'
            f'<tr><th style="{th};text-align:left;">{lbl}</th>'
            + "".join(f'<th style="{th}">{k}</th>' for k in keys) + '</tr>')
    rows = ""
    for p in players:
        nm = p.get("name","?")
        if kind == "Batting" and p.get("pos"): nm = f'{nm} {p["pos"]}'
        if kind == "Pitching" and p.get("note"): nm = f'{nm} ({p["note"]})'
        s = p.get("stats",{})
        rows += (f'<tr><td style="{td0}">{nm}</td>'
                 + "".join(f'<td style="{td}">{s.get(k,"-")}</td>' for k in keys)
                 + '</tr>')
    return (f'<div style="overflow-x:auto;margin-top:8px;">'
            f'<table style="border-collapse:collapse;width:100%;">{head}{rows}</table></div>')

def _mi_ordinal(n):
    try: n = int(n)
    except: return ""
    if n % 100 in (11, 12, 13): return "th"
    return {1:"st",2:"nd",3:"rd"}.get(n % 10, "th")

def _mi_agate(g):
    a = g.get("box_score",{}).get("agate",{})
    if not a: return ""
    parts = []
    def _line(lbl, items):
        if items: parts.append(f'<b style="color:{_I};">{lbl}:</b> ' + "; ".join(items))
    _line("HR", a.get("home_runs",[]))
    _line("2B", a.get("doubles",[]))
    _line("E",  a.get("errors",[]))
    lob = a.get("lob",{})
    if lob.get("away") or lob.get("home"):
        parts.append(f'<b style="color:{_I};">LOB:</b> '
                     f'{g.get("away_abbr","")} {lob.get("away","-")}, '
                     f'{g.get("home_abbr","")} {lob.get("home","-")}')
    top = " &nbsp; ".join(parts)
    sp = a.get("scoring_plays",[]); sc = ""
    if sp:
        items = ""
        for p in sp:
            arrow = "&#9650;" if p.get("half") == "top" else "&#9660;"
            inn = p.get("inning")
            inn_str = f'{inn}{_mi_ordinal(inn)}' if inn else "?"
            score = f' <span style="font-family:{_MONO};color:{_M};">{p.get("score","")}</span>' if p.get("score") else ""
            items += (f'<div style="padding:2px 0;">'
                      f'<span style="color:{_M};">{arrow}</span> '
                      f'<b style="color:{_I};">{inn_str}</b>{score} '
                      f'<span style="font-style:italic;">{p.get("text","")}</span></div>')
        sc = (f'<div style="margin-top:6px;">'
              f'<div style="font-family:{_SANS};font-size:11px;font-weight:bold;'
              f'text-transform:uppercase;letter-spacing:.06em;color:{_I};margin-bottom:2px;">'
              f'Scoring Plays</div>{items}</div>')
    if not top and not sc: return ""
    return (f'<div style="border-top:1px solid {_I};padding-top:5px;margin-top:4px;'
            f'font-family:{_SANS};font-size:11px;line-height:1.5;color:{_M};">{top}{sc}</div>')

def _mi_game(g):
    aw = g.get("away_team","").split()[-1]; hm = g.get("home_team","").split()[-1]
    as_ = g.get("away_score",0); hs = g.get("home_score",0)
    head = f"{aw} {as_}, {hm} {hs}" if as_ >= hs else f"{hm} {hs}, {aw} {as_}"
    box  = g.get("box_score",{})
    aw_a = g.get("away_abbr",""); hm_a = g.get("home_abbr","")
    away_b = box.get("away",{}); home_b = box.get("home",{})
    # Away team first (batting then pitching), then home team (batting then pitching)
    away_tables = (_mi_stat_table(aw_a, away_b.get("batting",[]), _MLB_BAT, "Batting")
                   + _mi_stat_table(aw_a, away_b.get("pitching",[]), _MLB_PIT, "Pitching"))
    home_tables = (_mi_stat_table(hm_a, home_b.get("batting",[]), _MLB_BAT, "Batting")
                   + _mi_stat_table(hm_a, home_b.get("pitching",[]), _MLB_PIT, "Pitching"))
    return (f'<div style="margin-bottom:24px;">'
            f'<div style="font-family:{_SANS};font-size:15px;font-weight:bold;color:{_I};'
            f'border-bottom:2px solid {_I};padding-bottom:3px;margin-bottom:4px;">{head}</div>'
            f'{_mi_linescore(g)}{away_tables}{home_tables}{_mi_agate(g)}</div>')

def _mi_yesterday_strip(games):
    """Compact 3-column results strip above full box scores."""
    if not games: return ""
    col = (f'<div style="display:inline-block;vertical-align:top;width:100%;max-width:208px;'
           f'box-sizing:border-box;padding:0 10px;font-size:0;">')
    cells = []
    for g in games:
        aw = g.get("away_team","").split()[-1]; hm = g.get("home_team","").split()[-1]
        a = g.get("away_score",0); h = g.get("home_score",0)
        aw_b = "font-weight:bold;" if a > h else ""; hm_b = "font-weight:bold;" if h > a else ""
        cells.append(f'<div style="padding:4px 0;border-bottom:.5px dotted {_H};'
                     f'font-family:{_SANS};font-size:13px;color:{_I};line-height:1.4;">'
                     f'<span style="{aw_b}">{aw} {a}</span>, '
                     f'<span style="{hm_b}">{hm} {h}</span></div>')
    per = (len(cells) + 2) // 3
    chunks = [cells[i:i+per] for i in range(0, len(cells), per)]
    html = f'<div style="font-size:0;line-height:0;text-align:left;">'
    for c in chunks:
        html += col + "".join(c) + '</div>'
    return html + '</div>'

def _mi_today_games(games):
    if not games: return ""

    def _fmt_et(iso):
        """Convert ISO UTC date string to 12-hour ET display (ET = UTC-4 in May)."""
        if not iso: return ""
        try:
            from datetime import datetime, timezone, timedelta
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            et = dt.astimezone(timezone(timedelta(hours=-4)))
            h, m = et.hour, et.minute
            suffix = "PM" if h >= 12 else "AM"
            h12 = h % 12 or 12
            return f'{h12}:{m:02d} {suffix} ET'
        except Exception:
            return ""

    items = []
    for g in games:
        aw = g.get("away_abbr") or g.get("away_team","").split()[-1]
        hm = g.get("home_abbr") or g.get("home_team","").split()[-1]
        prob = g.get("probables","")
        start = g.get("start","") or _fmt_et(g.get("date",""))
        items.append(
            f'<div style="padding:8px 0;border-bottom:.5px solid {_H};font-family:{_SANS};font-size:11px;">'
            f'<span style="color:{_I};">{aw} @ {hm}</span>'
            f'<span style="font-family:{_MONO};font-size:11px;color:{_M};float:right;">'
            f'{start}</span>'
            f'<div style="font-size:11px;color:{_M};clear:both;">{prob}</div></div>'
        )
    per = (len(items) + 1) // 2
    col = (f'<div style="display:inline-block;vertical-align:top;width:100%;max-width:312px;'
           f'box-sizing:border-box;padding:0 10px;font-size:0;line-height:0;">')
    return (f'<div style="font-size:0;line-height:0;text-align:left;">'
            f'{col}' + "".join(items[:per]) + f'</div>{col}' + "".join(items[per:]) + '</div></div>')

def _mi_sport_divider(label):
    """Bold newspaper sport-section divider, e.g. a heavy ruled band with the sport name."""
    return (f'<div style="border-top:4px solid {_I};border-bottom:2px solid {_I};'
            f'margin:30px 0 14px;padding:6px 0;">'
            f'<div style="font-family:{_SANS};font-size:20px;font-weight:bold;'
            f'letter-spacing:.03em;color:{_I};text-transform:uppercase;">{label}</div></div>')

def _mi_section_band(label):
    """Top-level band: PLAYOFFS / REGULAR SEASON / GOLF & TENNIS."""
    return (f'<div style="background:{_I};padding:5px 10px;margin:32px 0 16px;">'
            f'<span style="font-family:{_SANS};font-size:12px;font-weight:bold;'
            f'letter-spacing:.12em;color:#fff;text-transform:uppercase;">{label}</span></div>')

def _mi_game_headline(away, hs_away, home, hs_home, extra=""):
    return (f'<div style="font-family:{_SANS};font-size:15px;font-weight:bold;color:{_I};'
            f'border-bottom:2px solid {_I};padding-bottom:3px;margin-bottom:4px;">'
            f'{away} {hs_away}, {home} {hs_home}{extra}</div>')

def _mi_series_note(series, winner):
    if not series: return ""
    if series.get("series_over"):
        return (f'<div style="font-family:{_SANS};font-size:11px;font-weight:bold;'
                f'color:{_I};margin:2px 0 6px;">SERIES OVER &mdash; {winner} advances '
                f'&middot; {series.get("summary","")}</div>')
    return (f'<div style="font-family:{_SANS};font-size:11px;color:{_M};margin:2px 0 6px;">'
            f'Game {series.get("next_game_number","?")} next</div>')

_MI_NBA_KEYS = ["MIN","FG","3PT","FT","REB","AST","STL","BLK","PTS"]

def _mi_nba_player_table(abbr, players):
    if not players: return ""
    th  = (f'padding:2px 4px;text-align:right;font-family:{_SANS};font-size:10px;'
           f'font-weight:bold;color:{_I};border-bottom:1px solid {_I};white-space:nowrap;')
    cap = (f'padding:4px 2px 1px;text-align:left;font-family:{_SANS};font-size:12px;'
           f'font-weight:bold;color:{_I};border-bottom:2px solid {_I};letter-spacing:.04em;')
    td  = f'padding:1px 4px;text-align:right;border-bottom:.5px solid {_H};font-family:{_MONO};font-size:11px;color:{_I};'
    td0 = f'padding:1px 4px;text-align:left;border-bottom:.5px solid {_H};font-family:{_SANS};font-size:11px;color:{_I};white-space:nowrap;'
    head = (f'<tr><td style="{cap}" colspan="{len(_MI_NBA_KEYS)+2}">{abbr}</td></tr>'
            f'<tr><th style="{th};text-align:left;">Player</th><th style="{th}"></th>'
            + "".join(f'<th style="{th}">{k}</th>' for k in _MI_NBA_KEYS) + '</tr>')
    rows = ""
    for p in players:
        s = p.get("stats",{}); dot = "&bull; " if p.get("starter") else ""
        rows += (f'<tr><td style="{td0}">{dot}{p["name"]}</td>'
                 f'<td style="{td0}">{p.get("pos","")}</td>'
                 + "".join(f'<td style="{td}{";font-weight:bold;" if k=="PTS" else ""}">{s.get(k,"-")}</td>' for k in _MI_NBA_KEYS)
                 + '</tr>')
    return (f'<div style="overflow-x:auto;margin-top:8px;">'
            f'<table style="border-collapse:collapse;width:100%;min-width:360px;">{head}{rows}</table></div>')

def _mi_period_linescore(g, labels):
    box = g.get("box_score",{}); ls = box.get("linescore",{})
    ap = ls.get("away_periods"); hp = ls.get("home_periods")
    if not ap and not hp: return ""
    plabels = ls.get("period_labels", labels)
    aw = g.get("away_abbr","AWY"); hm = g.get("home_abbr","HOM")
    as_ = g.get("away_score",0); hs = g.get("home_score",0)
    n = len(plabels)
    hh = (f'padding:2px 5px;text-align:center;font-family:{_SANS};font-size:10px;'
          f'font-weight:bold;color:{_I};border-bottom:1px solid {_I};')
    hh0 = hh + 'text-align:left;'
    td  = f'padding:2px 5px;text-align:center;font-family:{_MONO};font-size:11px;border-bottom:.5px solid {_H};'
    td0 = f'padding:2px 5px;text-align:left;font-weight:bold;font-family:{_SANS};font-size:11px;white-space:nowrap;border-bottom:.5px solid {_H};'
    tot = f'{td}font-weight:bold;'
    hdr = f'<tr><th style="{hh0}"></th>' + "".join(f'<th style="{hh}">{p}</th>' for p in plabels)
    hdr += f'<th style="{hh}">T</th></tr>'
    def _row(abbr, periods, total, win):
        w = ";font-weight:bold;" if win else ""
        cells = "".join(f'<td style="{td}{w}">{periods[i] if i < len(periods) else ""}</td>' for i in range(n))
        return f'<tr><td style="{td0}">{abbr}</td>{cells}<td style="{tot}{w}">{total}</td></tr>'
    body = _row(aw, ap or [], as_, as_ > hs) + _row(hm, hp or [], hs, hs > as_)
    return (f'<div style="overflow-x:auto;margin:6px 0;">'
            f'<table style="border-collapse:collapse;min-width:280px;">{hdr}{body}</table></div>')

def _mi_nba_game(g):
    box = g.get("box_score",{})
    away = g.get("away_team","").split()[-1]; home = g.get("home_team","").split()[-1]
    aw_a = g.get("away_abbr",""); hm_a = g.get("home_abbr","")
    as_ = g.get("away_score",0); hs = g.get("home_score",0)
    winner = g.get("winner",""); ot = " (OT)" if g.get("overtime") else ""
    head = (f"{away} {as_}, {home} {hs}{ot}" if as_ >= hs
            else f"{home} {hs}, {away} {as_}{ot}")
    headline = (f'<div style="font-family:{_SANS};font-size:15px;font-weight:bold;color:{_I};'
                f'border-bottom:2px solid {_I};padding-bottom:3px;margin-bottom:4px;">{head}</div>')
    snote = _mi_series_note(g.get("series"), g.get("winner",""))
    ls = _mi_period_linescore(g, ["Q1","Q2","Q3","Q4"])
    away_side = box.get("away",{}); home_side = box.get("home",{})
    if not away_side.get("players") and not home_side.get("players"):
        for sd in box.values():
            if isinstance(sd,dict) and "players" in sd:
                if sd.get("team")==aw_a: away_side=sd
                elif sd.get("team")==hm_a: home_side=sd
    tables = _mi_nba_player_table(aw_a, away_side.get("players",[])) + _mi_nba_player_table(hm_a, home_side.get("players",[]))
    if not tables:
        tables = f'<p style="font-family:{_SANS};font-size:11px;color:{_M};padding:4px 0;">Stats not yet available.</p>'
    return f'<div style="margin-bottom:24px;">{headline}{snote}{ls}{tables}</div>'

_NHL_KEYS = ["TOI", "G", "A", "PTS", "+/-", "SOG", "PIM"]

def _mi_nhl_player_table(abbr, players):
    if not players: return ""
    th  = (f'padding:2px 4px;text-align:right;font-family:{_SANS};font-size:10px;'
           f'font-weight:bold;color:{_I};border-bottom:1px solid {_I};white-space:nowrap;')
    cap = (f'padding:4px 2px 1px;text-align:left;font-family:{_SANS};font-size:12px;'
           f'font-weight:bold;color:{_I};border-bottom:2px solid {_I};letter-spacing:.04em;')
    td  = f'padding:1px 4px;text-align:right;border-bottom:.5px solid {_H};font-family:{_MONO};font-size:11px;color:{_I};'
    td0 = f'padding:1px 4px;text-align:left;border-bottom:.5px solid {_H};font-family:{_SANS};font-size:11px;color:{_I};white-space:nowrap;'
    head = (f'<tr><td style="{cap}" colspan="{len(_NHL_KEYS)+2}">{abbr}</td></tr>'
            f'<tr><th style="{th};text-align:left;">Player</th><th style="{th}"></th>'
            + "".join(f'<th style="{th}">{k}</th>' for k in _NHL_KEYS) + '</tr>')
    rows = ""
    for p in players:
        s = p.get("stats",{})
        rows += (f'<tr><td style="{td0}">{p["name"]}</td>'
                 f'<td style="{td0}">{p.get("pos","")}</td>'
                 + "".join(f'<td style="{td}">{s.get(k,"-")}</td>' for k in _NHL_KEYS)
                 + '</tr>')
    return (f'<div style="overflow-x:auto;margin-top:8px;">'
            f'<table style="border-collapse:collapse;width:100%;min-width:320px;">{head}{rows}</table></div>')

def _mi_generic_game(g):
    """NHL and other period sports: headline + period linescore + skater stats if available."""
    box = g.get("box_score",{})
    away = g.get("away_team","").split()[-1]; home = g.get("home_team","").split()[-1]
    aw_a = g.get("away_abbr",""); hm_a = g.get("home_abbr","")
    as_ = g.get("away_score",0); hs = g.get("home_score",0)
    ot = " (OT)" if g.get("overtime") else ""
    head = (f"{away} {as_}, {home} {hs}{ot}" if as_ >= hs
            else f"{home} {hs}, {away} {as_}{ot}")
    headline = (f'<div style="font-family:{_SANS};font-size:15px;font-weight:bold;color:{_I};'
                f'border-bottom:2px solid {_I};padding-bottom:3px;margin-bottom:4px;">{head}</div>')
    snote = _mi_series_note(g.get("series"), g.get("winner",""))
    ls = _mi_period_linescore(g, ["P1","P2","P3"])
    away_side = box.get("away",{}); home_side = box.get("home",{})
    tables = (_mi_nhl_player_table(aw_a, away_side.get("players",[]))
              + _mi_nhl_player_table(hm_a, home_side.get("players",[])))
    return f'<div style="margin-bottom:20px;">{headline}{snote}{ls}{tables}</div>'

def _mi_bracket(bracket_data):
    if not bracket_data: return ""
    rounds = ""
    for rnd in bracket_data:
        series = rnd.get("series",[])
        if not series: continue
        cards = ""
        for s in series:
            a = s.get("away_abbr","?"); h = s.get("home_abbr","?")
            aw = s.get("away_wins",0); hw = s.get("home_wins",0)
            summ = s.get("summary",""); done = s.get("series_over",False)
            a_b = ";font-weight:bold;" if aw >= hw else ""
            h_b = ";font-weight:bold;" if hw >= aw else ""
            cards += (f'<div style="display:inline-block;vertical-align:top;min-width:130px;'
                      f'margin:0 16px 10px 0;font-family:{_SANS};font-size:13px;color:{_I};">'
                      f'<div style="display:flex;justify-content:space-between;{a_b}"><span>{a}</span>'
                      f'<span style="font-family:{_MONO};font-weight:bold;">{aw}</span></div>'
                      f'<div style="display:flex;justify-content:space-between;{h_b}"><span>{h}</span>'
                      f'<span style="font-family:{_MONO};font-weight:bold;">{hw}</span></div></div>')
        rounds += cards
    if not rounds: return ""
    return (f'<div style="font-family:{_SANS};font-size:11px;font-weight:bold;'
            f'text-transform:uppercase;letter-spacing:.06em;color:{_I};'
            f'border-bottom:1px solid {_I};padding-bottom:2px;margin:6px 0 8px;">Playoff Bracket</div>'
            f'<div style="font-size:0;">{rounds}</div>')

def _render_sport_inline(sport_key, sport_data, in_playoffs):
    """Inline-styled section for non-MLB sports (NBA/NHL/WNBA + playoffs/regular)."""
    label = sport_data.get("label", sport_key.upper())
    games = [g for g in sport_data.get("yesterday_games",[]) if g.get("completed")]
    standings = sport_data.get("standings",{})
    bracket = sport_data.get("bracket",[])
    leaders = sport_data.get("leaders",{})
    if not games and not standings and not bracket:
        return ""
    html = _mi_sport_divider(label)
    if in_playoffs:
        if bracket: html += _mi_bracket(bracket)
        for g in games:
            if sport_key in ("nba","wnba"): html += _mi_nba_game(g)
            elif sport_key == "mlb":        html += _mi_game(g)
            else:                            html += _mi_generic_game(g)
    else:
        # Regular season non-MLB: standings (+ scores strip). Leaders only if present.
        if isinstance(standings, list) and standings:
            html += _mi_simple_standings(standings)
        if games:
            html += _mi_rule("Scores")
            html += _mi_yesterday_strip(games)
    return html

def _mi_simple_standings(teams):
    """Flat (non-divisional) standings for NBA/NHL/WNBA, trimmed for email width."""
    th = (f'padding:2px 4px;border-bottom:1px solid {_I};font-family:{_SANS};font-size:10px;'
          f'font-weight:bold;color:{_M};text-align:right;white-space:nowrap;')
    th0 = th + 'text-align:left;'
    td  = f'padding:1px 4px;border-bottom:.5px solid {_H};font-family:{_MONO};font-size:11px;text-align:right;color:{_I};white-space:nowrap;'
    td0 = f'padding:1px 4px;border-bottom:.5px solid {_H};font-family:{_SANS};font-size:12px;text-align:left;color:{_I};white-space:nowrap;'
    hdr = (f'<tr><th style="{th0}">Team</th><th style="{th}">W</th><th style="{th}">L</th>'
           f'<th style="{th}">Pct</th><th style="{th}">GB</th><th style="{th}">Strk</th></tr>')
    rows = ""
    for t in teams[:16]:
        gb = str(t.get("games_behind","")); gb = "" if gb in ("-","0","0.0","") else gb
        rows += (f'<tr><td style="{td0}">{t.get("team","?")}</td>'
                 f'<td style="{td}">{t.get("wins","")}</td><td style="{td}">{t.get("losses","")}</td>'
                 f'<td style="{td}">{t.get("win_pct","")}</td><td style="{td}">{gb}</td>'
                 f'<td style="{td}">{t.get("streak","")}</td></tr>')
    return f'<table style="border-collapse:collapse;width:100%;margin-bottom:8px;">{hdr}{rows}</table>'

def _render_mlb_sections(sport_data):
    """
    Inline-styled MLB regular-season section.
    Layout (sequential, each clearly labeled):
      AL Standings → AL Leaders → NL Standings → NL Leaders
      → Yesterday's Results → Box Scores → Today's Games
    All styles inline — renders correctly in --append email context.
    """
    standings = sport_data.get("standings", {})
    leaders   = sport_data.get("leaders", {})
    games     = [g for g in sport_data.get("yesterday_games",[]) if g.get("completed")]
    today     = sport_data.get("today_games", [])
    today_sched = [g for g in today if not g.get("completed")]

    def _league_sections(full_name, div_set, team_set, lead_cats):
        """Emit standings then leaders as separate labeled sections for one league."""
        out = ""
        std = _mi_std_half(standings, div_set) if isinstance(standings, dict) else ""
        if std:
            out += _mi_rule(f"{full_name} Standings")
            out += std
        ldr = _mi_leaders_half(leaders, team_set, lead_cats)
        if ldr:
            out += _mi_rule(f"{full_name} Leaders")
            out += ldr
        return out

    html = ""
    html += _league_sections("American League", AL_DIVS, _MLB_AL, _MLB_LEAD_LEFT + _MLB_LEAD_RIGHT)
    html += _league_sections("National League", NL_DIVS, _MLB_NL, _MLB_LEAD_LEFT + _MLB_LEAD_RIGHT)
    if games:
        html += _mi_rule("Yesterday\'s Results")
        html += _mi_yesterday_strip(games)
        html += _mi_rule("Box Scores")
        for g in games:
            html += _mi_game(g)
    if today_sched:
        html += _mi_rule("Today\'s Games")
        html += _mi_today_games(today_sched)
    return html

def _date_display(game_state):
    yesterday=game_state.get("yesterday_date","")
    try:
        return datetime.strptime(yesterday,"%Y-%m-%d").strftime("%A, %B %#d, %Y")
    except:
        return yesterday

def _masthead(date_display, subtitle=""):
    sub = (f'<div style="font-family:{_SANS};font-size:13px;font-weight:bold;color:{_M};'
           f'margin-top:2px;text-transform:uppercase;letter-spacing:.05em;">{subtitle}</div>') if subtitle else ""
    return (f'<div style="text-align:center;border-top:4px solid {_I};border-bottom:1px solid {_I};'
            f'padding:10px 0 8px;margin-bottom:6px;">'
            f'<div style="font-family:Georgia,serif;font-size:30px;font-weight:bold;'
            f'font-style:italic;letter-spacing:.03em;color:{_I};">The Box Score</div>'
            f'<div style="font-family:{_SANS};font-size:14px;font-weight:bold;color:{_I};margin-top:3px;">{date_display}</div>'
            f'{sub}'
            f'<div style="font-family:{_SANS};font-size:11px;color:{_M};margin-top:2px;">Data via ESPN</div></div>')

def _footer():
    ts=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (f'<div style="font-family:{_SANS};font-size:10px;color:#aaa;text-align:center;'
            f'border-top:1px solid {_H};padding-top:8px;margin-top:20px;">Generated {ts}</div>')

def _sport_has_data(data):
    all_games=data.get("yesterday_games",[])+data.get("today_games",[])
    completed=[g for g in data.get("yesterday_games",[]) if g.get("completed")]
    in_playoffs=any(g.get("playoffs") for g in all_games) or bool(data.get("bracket",[]))
    return in_playoffs, bool(in_playoffs or completed or data.get("standings"))

def _ordered_sport_keys(game_state):
    """Sport keys with data, ordered playoffs-first then regular season, each
    group following SPORT_ORDER (so NBA before NHL, etc.). Mirrors the section
    ordering of the combined block so the per-sport images attach in the same
    sequence the reader expects."""
    sports=game_state.get("sports",{})
    playoff,regular=[],[]
    for key in SPORT_ORDER:
        if key not in sports: continue
        inp,has=_sport_has_data(sports[key])
        if inp: playoff.append(key)
        elif has: regular.append(key)
    return playoff+regular

def build_box_score_block_for_sport(game_state, sport_key, bare=False):
    """Render a standalone box score block for ONE sport, so each can become its
    own small, pasteable image. Returns '' if the sport has no data today.

    bare=True omits the masthead ("The Box Score", date, sport label, "Data via
    ESPN") and footer, leaving only the content tables — the newsletter already
    carries a "Box Scores" header, so that chrome is redundant in each image."""
    sports=game_state.get("sports",{})
    if sport_key not in sports:
        return ""
    data=sports[sport_key]
    in_playoffs, has_data = _sport_has_data(data)
    if not has_data:
        return ""
    if sport_key=="mlb":
        content=_render_mlb_sections(data) if bare else _mi_sport_divider("MLB")+_render_mlb_sections(data)
    else:
        content=_render_sport_inline(sport_key,data,in_playoffs=in_playoffs)
    if not content.strip():
        return ""
    if bare:
        return f'<div style="max-width:600px;margin:0 auto;background:#fff;">{content}</div>'
    masthead=_masthead(_date_display(game_state), subtitle=sport_key.upper())
    return f'<div style="max-width:600px;margin:0 auto;background:#fff;">{masthead}{content}{_footer()}</div>'

def build_golf_tennis_block(game_state, bare=False):
    """Render golf + tennis as their own image block. Returns '' if neither present."""
    golf=game_state.get("golf",[]); tennis=game_state.get("tennis",[])
    if not (golf or tennis):
        return ""
    content="".join(render_golf_html(t) for t in golf)+"".join(render_tennis_html(t) for t in tennis)
    if not content.strip():
        return ""
    if bare:
        return f'<div style="max-width:600px;margin:0 auto;background:#fff;">{content}</div>'
    masthead=_masthead(_date_display(game_state), subtitle="Golf &amp; Tennis")
    return f'<div style="max-width:600px;margin:0 auto;background:#fff;">{masthead}{content}{_footer()}</div>'

def build_mlb_chunk_blocks(game_state, games_per_chunk=4, bare=False):
    """MLB has a full daily slate, so a single image would be enormous. Split it
    into: one summary image (standings + leaders + results strip + today's
    games) and box scores chunked ~games_per_chunk per image. Returns a list of
    complete standalone-ready blocks.

    bare=True omits the masthead/footer chrome and the per-chunk "MLB Box Scores
    (n/N)" label, leaving only the tables (the newsletter carries the header)."""
    sports=game_state.get("sports",{})
    if "mlb" not in sports: return []
    data=sports["mlb"]
    standings=data.get("standings",{}); leaders=data.get("leaders",{})
    games=[g for g in data.get("yesterday_games",[]) if g.get("completed")]
    today_sched=[g for g in data.get("today_games",[]) if not g.get("completed")]
    date_display=_date_display(game_state)

    def _wrap(subtitle, inner):
        if bare:
            return f'<div style="max-width:600px;margin:0 auto;background:#fff;">{inner}</div>'
        return f'<div style="max-width:600px;margin:0 auto;background:#fff;">{_masthead(date_display, subtitle=subtitle)}{inner}{_footer()}</div>'

    def _league_sections(full_name,div_set,team_set,lead_cats):
        out=""
        std=_mi_std_half(standings,div_set) if isinstance(standings,dict) else ""
        if std: out+=_mi_rule(f"{full_name} Standings")+std
        ldr=_mi_leaders_half(leaders,team_set,lead_cats)
        if ldr: out+=_mi_rule(f"{full_name} Leaders")+ldr
        return out

    blocks=[]
    summary=""
    summary+=_league_sections("American League",AL_DIVS,_MLB_AL,_MLB_LEAD_LEFT+_MLB_LEAD_RIGHT)
    summary+=_league_sections("National League",NL_DIVS,_MLB_NL,_MLB_LEAD_LEFT+_MLB_LEAD_RIGHT)
    if games: summary+=_mi_rule("Yesterday's Results")+_mi_yesterday_strip(games)
    if today_sched: summary+=_mi_rule("Today's Games")+_mi_today_games(today_sched)
    if summary.strip(): blocks.append(_wrap("MLB — Standings & Leaders", summary))

    total=len(games)
    if total:
        nchunks=(total+games_per_chunk-1)//games_per_chunk
        for i in range(0,total,games_per_chunk):
            idx=i//games_per_chunk+1
            label=f"MLB Box Scores ({idx}/{nchunks})" if nchunks>1 else "MLB Box Scores"
            inner=("" if bare else _mi_rule(label))+"".join(_mi_game(g) for g in games[i:i+games_per_chunk])
            blocks.append(_wrap(label, inner))
    return blocks

def build_box_score_block(game_state):
    sports=game_state.get("sports",{}); golf=game_state.get("golf",[]); tennis=game_state.get("tennis",[])
    date_display=_date_display(game_state)
    playoff_keys=[]; regular_keys=[]
    for key in SPORT_ORDER:
        if key not in sports: continue
        data=sports[key]; all_games=data.get("yesterday_games",[])+data.get("today_games",[]); bracket=data.get("bracket",[])
        completed=[g for g in data.get("yesterday_games",[]) if g.get("completed")]
        if any(g.get("playoffs") for g in all_games) or bracket: playoff_keys.append(key)
        elif completed or data.get("standings"): regular_keys.append(key)
    content=""
    if playoff_keys:
        content+=_mi_section_band("Playoffs")
        for key in playoff_keys: content+=_render_sport_inline(key,sports[key],in_playoffs=True)
    if regular_keys:
        content+=_mi_section_band("Regular Season")
        for key in regular_keys:
            if key=="mlb":
                content+=_mi_sport_divider("MLB")
                content+=_render_mlb_sections(sports[key])
            else: content+=_render_sport_inline(key,sports[key],in_playoffs=False)
    if golf or tennis:
        content+=_mi_section_band("Golf &amp; Tennis")
        for t in golf: content+=render_golf_html(t)
        for t in tennis: content+=render_tennis_html(t)
    if not content.strip(): content=f'<p style="font-family:{_SANS};color:{_M};padding:20px 0;">No data for {date_display}.</p>'
    masthead=_masthead(date_display)
    return f'<div style="max-width:600px;margin:0 auto;background:#fff;">{masthead}{content}{_footer()}</div>'

def build_standalone(block):
    # Lock the page to a fixed mobile width so the rendered image is always
    # phone-sized. !important overrides the block's inline max-width:600px.
    img_lock = (
        f"html,body{{width:{MOBILE_IMG_WIDTH}px;min-width:{MOBILE_IMG_WIDTH}px;"
        f"max-width:{MOBILE_IMG_WIDTH}px;margin:0;padding:0;background:#fff;}}"
        f"body>div{{max-width:{MOBILE_IMG_WIDTH}px!important;}}"
        f"table{{max-width:100%;}}"
    )
    return f'<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="UTF-8"/>\n<meta name="viewport" content="width=device-width,initial-scale=1.0"/>\n<title>The Box Score</title>\n<style>\n{PAGE_CSS}\n{img_lock}\n</style>\n</head>\n<body>\n{block}\n</body>\n</html>'

def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--standalone",action="store_true"); parser.add_argument("--append",action="store_true"); parser.add_argument("--per-sport",action="store_true",dest="per_sport"); args=parser.parse_args()
    if not GAME_STATE_PATH.exists(): raise SystemExit("game_state.json not found.")
    game_state=json.loads(GAME_STATE_PATH.read_text(encoding="utf-8"))
    if args.per_sport:
        # One standalone HTML per sport with data, so each renders to its own
        # small, pasteable image. The workflow globs box_score_*.html → .jpg.
        # Numeric prefix locks attach/render order: playoffs first, then regular
        # season (NBA before NHL, WNBA last), MLB chunks in sequence, golf/tennis
        # at the end. sorted() in the email and the shell glob both honor it.
        written=[]; seq=1
        def _write(key_label, blk):
            nonlocal seq
            out=SCRIPT_DIR/f"box_score_sport_{seq:02d}_{key_label}.html"; seq+=1
            out.write_text(build_standalone(blk),encoding="utf-8"); written.append(out.name); print(f"✓ {out}")
        for key in _ordered_sport_keys(game_state):
            if key=="mlb":
                # MLB: summary image + box scores chunked ~4 games per image.
                for blk in build_mlb_chunk_blocks(game_state,bare=True):
                    _write("mlb", blk)
                continue
            blk=build_box_score_block_for_sport(game_state,key,bare=True)
            if blk: _write(key, blk)
        gt=build_golf_tennis_block(game_state,bare=True)
        if gt: _write("golf_tennis", gt)
        if not written: print("⚠ No sports had data — no per-sport images generated.")
        return
    block=build_box_score_block(game_state)
    if args.standalone:
        STANDALONE_OUTPUT.write_text(build_standalone(block),encoding="utf-8"); print(f"✓ {STANDALONE_OUTPUT}")
    elif args.append:
        if not EMAIL_PATH.exists(): raise SystemExit("newsletter_email.html not found.")
        html=EMAIL_PATH.read_text(encoding="utf-8")
        updated=html.replace("</body>",block+"\n</body>",1) if "</body>" in html else html+"\n"+block
        EMAIL_PATH.write_text(updated,encoding="utf-8"); print(f"✓ Appended to {EMAIL_PATH}")
    else:
        BLOCK_OUTPUT.write_text(block,encoding="utf-8"); print(f"✓ {BLOCK_OUTPUT}")

if __name__=="__main__":
    main()
