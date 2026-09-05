"""
JRA競馬場ガイド - 別ページ生成
10場それぞれのコース特徴(1周距離・直線・高低差など)と、簡易コース図(オリジナル作図)を
docs/courses.html として出力する。

【出典についての注意】
数値・コース構成はJRA公式サイトの「コース紹介」ページ(jra.go.jp/facilities/race/各場/course/)
などの一次情報を基にしている。ただし文章はすべて要約・自分の言葉で書き直したもので、
コース図もJRAの図をそのまま模写したものではなく、方向・直線位置・内外コースの有無など
構造的な特徴を踏まえて独自に簡略化して描いたスキーマ図。正確な図はJRA公式サイトを参照。
"""

import json
import os

# 実データ集計(build_course_stats.pyが出力)を読み込む
STATS_PATH = "data/course_stats.json"
COURSE_STATS = {}
if os.path.exists(STATS_PATH):
    with open(STATS_PATH, encoding="utf-8") as f:
        COURSE_STATS = json.load(f)


# 各競馬場のデータ(JRA公式コース紹介ページなどを基に要約)
COURSES = {
    "01": dict(name="札幌", direction="right", has_inner=False, extra=None,
               turf_lap="1640.9m", turf_straight="266.1m", elevation="0.7m(ほぼ平坦)",
               dirt_lap="1487m", dirt_straight="264.3m",
               feature="コース全体が丸みを帯びた円形に近い形状で、4コーナーとも半径が大きく緩やか。"
                       "直線は短く、コースに占めるコーナーの割合が大きい。芝は全面が洋芝でスパイラルカーブは不採用。",
               tendency="平坦+コーナー大回りのため、基本的に逃げ・先行馬が有利。"),
    "02": dict(name="函館", direction="right", has_inner=False, extra=None,
               turf_lap="1626.6m", turf_straight="262.1m", elevation="3.5m",
               dirt_lap="1475.8m", dirt_straight="260.3m",
               feature="3・4コーナーにスパイラルカーブを採用。2コーナーから4コーナーにかけてなだらかに上り、"
                       "そこから直線にかけて下る、小高い丘のようなレイアウト。芝は洋芝。",
               tendency="開催後半は洋芝の傷みが進みやすく、道悪・パワー適性が結果を左右しやすい。"),
    "03": dict(name="福島", direction="right", has_inner=False, extra=None,
               turf_lap="1600m(JRA最小)", turf_straight="292m", elevation="1.9m",
               dirt_lap="1444.6m", dirt_straight="295.7m",
               feature="1周する間に上り下りを2回繰り返すユニークな起伏構成。3・4コーナーにスパイラルカーブ。",
               tendency="小回りで先行有利。開催が進むと外差しも決まりやすくなる。"),
    "04": dict(name="新潟", direction="left", has_inner=True, extra="senchoku",
               turf_lap="外回り2223m(日本最長)/内回り1623m", turf_straight="外回り658.7m(日本最長)/内回り353.9m",
               elevation="外回り1.6m/内回りほぼ平坦(0.8m)",
               dirt_lap="—", dirt_straight="353.9m(ほぼ平坦)",
               feature="日本で唯一、直線だけの1000mコース(通称「千直」)を持つ。2001年の改修で右回りから左回りに変更され、"
                       "外回りコースは1周・直線ともJRA最長の規模になった。",
               tendency="長い直線ではしっかりした決め手が必要。千直は追い風なら逃げ・先行、向かい風なら差し・追い込みが有利になりやすい。"),
    "05": dict(name="東京", direction="left", has_inner=False, extra=None,
               turf_lap="2083.1m", turf_straight="525.9m(全場最長)", elevation="2.0m",
               dirt_lap="1899m(日本最大)", dirt_straight="501.6m(日本最大)",
               feature="「日本競馬の顔」と呼ばれるスケールの大きいチャンピオンコース。1コーナーから向正面にかけて"
                       "長い下り、3コーナー手前で上り、さらに直線残り460〜300m付近にも上り坂がある「2段坂」構成。"
                       "ダートコースも1周・直線ともに日本最大級のスケールを持つ。",
               tendency="スケールが大きく紛れが少ないため、実力がストレートに反映されやすい。"),
    "06": dict(name="中山", direction="right", has_inner=True, extra="onigiri",
               turf_lap="内回り1667.1m/外回り1839.7m", turf_straight="310m(4大場で最短)",
               elevation="5.3m(JRA全場で最大)",
               dirt_lap="1493m", dirt_straight="308m(4大場で最短)",
               feature="内回りコースの外側に外回りコースが後から追加された構造のため、コース全体は左右非対称な"
                       "「おにぎり型」に近い形状になっている(実際、芝外回り1200mのスタート地点は"
                       "「おむすびの頂点」と表現されることがある)。ダートコースは芝内回りのさらに内側にある。"
                       "ゴール前残り180〜70m地点には最大勾配2.24%(JRA最大)の急坂があり、\"中山名物\"として知られる。",
               tendency="急坂とタイトな直線により、パワー・スタミナと坂への対応力が問われる。ダートも高低差4.5mと"
                        "タフで、逃げ・先行馬が圧倒的に有利。"),
    "07": dict(name="中京", direction="left", has_inner=False, extra=None,
               turf_lap="阪神・中山の内回りより長い規模", turf_straight="東京に次ぐ長さ", elevation="芝3.5m/ダート3.4m",
               dirt_lap="1530m", dirt_straight="410.7m(東京に次ぐ長さ)",
               feature="ゴール地点からなだらかな上りが続き、向正面半ばで最高点に到達。3・4コーナーはスパイラルカーブで、"
                       "直線入口すぐに勾配約2%の急坂がある。",
               tendency="急坂の存在で差し・追い込み馬が水準以上に活躍しやすい。"),
    "08": dict(name="京都", direction="right", has_inner=True, extra=None,
               turf_lap="内回り・外回りが3コーナーで分岐、4コーナーで合流", turf_straight="外回り400m台/内回り328m",
               elevation="外回り4.3m(全場2位)/内回り3.1m",
               dirt_lap="1607.6m(東京に次ぐ規模)", dirt_straight="329.1m",
               feature="3コーナー付近の\"坂\"が名物。外回りは向正面半ばから3コーナーにかけて上り、"
                       "4コーナーにかけて一気に下るレイアウト。",
               tendency="坂の攻略力がカギ。ダートは起伏の影響で上がりの速い決着になりやすい。"),
    "09": dict(name="阪神", direction="right", has_inner=True, extra=None,
               turf_lap="内回り1689m/外回り約2089m(右回り最長)", turf_straight="外回り473.6m(右回り最長級)/内回り356.5m",
               elevation="外回り2.4m/内回り1.9m",
               dirt_lap="1517.6m", dirt_straight="352.7m",
               feature="ゴール前に勾配1.5%の急坂。内回りは残り800m、外回りは残り600m付近から直線半ばにかけて"
                       "なだらかな下りが続き、その勢いのまま急坂に向かうレイアウト。ダートも残り200m付近に"
                       "高低差1.6mの上り坂がある。",
               tendency="坂を苦にしないパワーとスピードの両立が必要。"),
    "10": dict(name="小倉", direction="right", has_inner=False, extra=None,
               turf_lap="1615.1m(福島に次いで小さい)", turf_straight="293m", elevation="3.0m",
               dirt_lap="1445.4m", dirt_straight="291.3m",
               feature="2コーナー付近が最高地点で、そこから向正面・3〜4コーナーにかけて下り、直線は完全に平坦。"
                       "幅員30mとローカル場としては広め。",
               tendency="前半のペースが速くなりやすく、逃げ・先行馬が明確に有利。"),
}


def track_svg(code, c):
    """コース図。芝(緑)・ダート(橙)を塗りつぶしの帯として描き、方向・内外コース・特殊区間を示す。
    発走地点ごとの正確な位置関係までは再現していない(正確な図はJRA公式サイトを参照)。"""
    direction_label = "右回り" if c["direction"] == "right" else "左回り"
    arrow = "↻" if c["direction"] == "right" else "↺"
    has_dirt = c["dirt_lap"] != "—" or c["dirt_straight"] != "—"

    TURF = "#2E6B4F"
    TURF_IN = "#8FBFA3"
    DIRT = "#D08A3E"

    def oval(cx, cy, rx, ry, straight):
        """角丸トラック形状のパスを返す。straight=直線部の半幅"""
        return (f"M{cx-straight},{cy-ry} L{cx+straight},{cy-ry} "
                f"A{rx},{ry} 0 0 1 {cx+straight},{cy+ry} "
                f"L{cx-straight},{cy+ry} A{rx},{ry} 0 0 1 {cx-straight},{cy-ry} Z")

    if code == "06":
        # 中山: 内回りの外側に外回りが3-4コーナー側(右)へ張り出す非対称形("おにぎり型")
        # 基本の角丸トラックを描き、外回りだけ右側に円を重ねて膨らませる
        shapes = f'''
          <g>
            <path d="{oval(146,76,44,46,58)}" fill="{TURF}"/>
            <circle cx="212" cy="62" r="36" fill="{TURF}"/>
          </g>
          <path d="{oval(146,76,32,34,56)}" fill="{TURF_IN}"/>
          <path d="{oval(146,76,23,25,50)}" fill="{DIRT}"/>
          <path d="{oval(146,76,14,15,44)}" fill="#FFFFFF"/>
          <text x="212" y="40" font-size="9" fill="#FFFFFF" font-weight="bold" text-anchor="middle">芝外</text>
          <text x="146" y="54" font-size="9" fill="#2A5540" font-weight="bold" text-anchor="middle">芝内</text>
          <text x="146" y="80" font-size="8.5" fill="#A8631E" font-weight="bold" text-anchor="middle">ダート</text>'''
        legend_dirt = True

    elif code == "04":
        # 新潟: 外回り+内回り+直線1000m(千直)+ダート
        shapes = f'''
          <path d="{oval(150,78,46,50,72)}" fill="{TURF}"/>
          <path d="{oval(150,78,32,36,72)}" fill="{TURF_IN}"/>
          <path d="{oval(150,78,24,27,66)}" fill="{DIRT}"/>
          <path d="{oval(150,78,15,17,60)}" fill="#FFFFFF"/>
          <line x1="14" y1="78" x2="286" y2="78" stroke="#C9A227" stroke-width="5" stroke-linecap="round"/>
          <text x="98" y="74" font-size="8.5" fill="#8A6E10" font-weight="bold">千直(直線1000m)</text>
          <text x="245" y="36" font-size="9" fill="#1F4D3A" font-weight="bold">芝外</text>
          <text x="150" y="52" font-size="9" fill="#2A5540" font-weight="bold" text-anchor="middle">芝内</text>
          <text x="150" y="82" font-size="9" fill="#A8631E" font-weight="bold" text-anchor="middle">ダート</text>'''
        legend_dirt = True

    elif c["has_inner"]:
        # 内回り・外回りを持つ場(京都・阪神)
        shapes = f'''
          <path d="{oval(150,78,46,50,72)}" fill="{TURF}"/>
          <path d="{oval(150,78,33,37,70)}" fill="{TURF_IN}"/>'''
        legend_dirt = has_dirt
        if has_dirt:
            shapes += f'''
          <path d="{oval(150,78,24,27,64)}" fill="{DIRT}"/>
          <path d="{oval(150,78,14,16,58)}" fill="#FFFFFF"/>
          <text x="150" y="82" font-size="9" fill="#A8631E" font-weight="bold" text-anchor="middle">ダート</text>'''
        else:
            shapes += f'<path d="{oval(150,78,24,27,64)}" fill="#FFFFFF"/>'
        shapes += f'''
          <text x="243" y="36" font-size="9" fill="#1F4D3A" font-weight="bold">芝外</text>
          <text x="150" y="52" font-size="9" fill="#2A5540" font-weight="bold" text-anchor="middle">芝内</text>'''

    else:
        shapes = f'<path d="{oval(150,78,46,50,72)}" fill="{TURF}"/>'
        legend_dirt = has_dirt
        if has_dirt:
            shapes += f'''
          <path d="{oval(150,78,32,36,66)}" fill="{DIRT}"/>
          <path d="{oval(150,78,20,23,58)}" fill="#FFFFFF"/>
          <text x="150" y="82" font-size="9" fill="#A8631E" font-weight="bold" text-anchor="middle">ダート</text>'''
        else:
            shapes += f'<path d="{oval(150,78,32,36,66)}" fill="#FFFFFF"/>'
        shapes += f'<text x="150" y="41" font-size="9" fill="#FFFFFF" font-weight="bold" text-anchor="middle">芝</text>'

    return f'''<svg viewBox="0 0 300 160" class="course-svg">
      {shapes}
      <line x1="88" y1="{"120" if code == "06" else "124"}" x2="212" y2="{"120" if code == "06" else "124"}" stroke="#C0392B" stroke-width="4" stroke-linecap="round"/>
      <text x="150" y="150" font-size="9.5" fill="#C0392B" text-anchor="middle" font-weight="bold">ゴール前直線</text>
      <text x="12" y="19" font-size="17" fill="#1F4D3A">{arrow}</text>
      <text x="32" y="19" font-size="10" fill="#6B5F4D">{direction_label}</text>
    </svg>
    <p class="course-legend">
      <span><i style="background:{TURF}"></i>芝コース</span>
      {f'<span><i style="background:{DIRT}"></i>ダートコース</span>' if legend_dirt else ''}
    </p>'''


# 各競馬場の高低差プロファイル(芝コース)
# JRA公式のコース紹介などに記載された起伏構成の説明を、
# 「ゴール→1〜2コーナー→向正面→3〜4コーナー→直線→ゴール」の順で相対的な高さ(m)に置き換えたもの。
# 実測の断面データではなく、記述されている上り下りの順序と高低差の大きさを反映した概形。
ELEVATION_PROFILES = {
    # (位置の割合0-1, 相対高さm) のリスト。0=ゴール地点、1=1周してゴールに戻る
    "01": [(0.0, 0.0), (0.25, 0.2), (0.5, 0.4), (0.75, 0.3), (0.9, 0.1), (1.0, 0.0)],
    "02": [(0.0, 0.5), (0.2, 0.0), (0.45, 1.8), (0.7, 3.5), (0.85, 1.5), (1.0, 0.5)],
    "03": [(0.0, 1.2), (0.2, 0.0), (0.45, 1.3), (0.6, 1.3), (0.78, 0.3), (0.9, 0.0), (0.96, 1.2), (1.0, 1.2)],
    "04": [(0.0, 0.6), (0.25, 0.6), (0.45, 0.8), (0.6, 2.2), (0.8, 0.6), (1.0, 0.6)],
    "05": [(0.0, 2.0), (0.15, 1.5), (0.4, 0.1), (0.55, 1.6), (0.68, 1.2), (0.78, 0.6), (0.88, 0.6), (0.94, 2.0), (1.0, 2.0)],
    "06": [(0.0, 3.1), (0.15, 5.0), (0.28, 5.3), (0.45, 3.0), (0.65, 1.2), (0.8, 0.5), (0.88, 0.9), (0.94, 3.1), (1.0, 3.1)],
    "07": [(0.0, 1.5), (0.2, 2.4), (0.45, 3.5), (0.62, 2.6), (0.78, 1.0), (0.86, 0.4), (0.93, 2.4), (1.0, 1.5)],
    "08": [(0.0, 0.6), (0.2, 0.6), (0.42, 0.8), (0.58, 4.3), (0.75, 0.9), (0.88, 0.6), (1.0, 0.6)],
    "09": [(0.0, 1.8), (0.18, 2.2), (0.42, 2.4), (0.6, 2.2), (0.75, 1.0), (0.86, 0.2), (0.93, 1.8), (1.0, 1.8)],
    "10": [(0.0, 1.4), (0.15, 2.7), (0.25, 3.0), (0.45, 1.8), (0.65, 0.9), (0.82, 0.4), (0.9, 1.4), (1.0, 1.4)],
}


def elevation_svg(code, c):
    """高低差プロファイル図。上り区間を赤、下り区間を青で描く。"""
    prof = ELEVATION_PROFILES.get(code)
    if not prof:
        return ""

    W, H = 300, 92
    PAD_L, PAD_R, PAD_T, PAD_B = 8, 8, 14, 22
    plot_w = W - PAD_L - PAD_R
    plot_h = H - PAD_T - PAD_B
    max_h = max(h for _, h in prof)
    max_h = max(max_h, 1.0)

    def xy(pos, h):
        x = PAD_L + pos * plot_w
        y = PAD_T + plot_h - (h / max_h) * plot_h
        return x, y

    # 区間ごとに上り(赤)・下り(青)・平坦(灰)で色分け
    segments = ""
    for i in range(len(prof) - 1):
        (p1, h1), (p2, h2) = prof[i], prof[i + 1]
        x1, y1 = xy(p1, h1)
        x2, y2 = xy(p2, h2)
        diff = h2 - h1
        if diff > 0.15:
            color, width = "#B0433A", 3          # 上り
        elif diff < -0.15:
            color, width = "#1E5FA8", 3          # 下り
        else:
            color, width = "#8B8378", 2.5        # ほぼ平坦
        segments += f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{color}" stroke-width="{width}" stroke-linecap="round"/>'

    # 位置の目安ラベル
    marks = [(0.0, "ゴール"), (0.25, "1-2角"), (0.5, "向正面"), (0.78, "4角"), (1.0, "ゴール")]
    labels = ""
    for pos, name in marks:
        x = PAD_L + pos * plot_w
        labels += f'<line x1="{x:.1f}" y1="{PAD_T}" x2="{x:.1f}" y2="{PAD_T+plot_h}" stroke="#E4DAC0" stroke-width="1"/>'
        anchor = "start" if pos == 0.0 else ("end" if pos == 1.0 else "middle")
        labels += f'<text x="{x:.1f}" y="{H-9}" font-size="7.5" fill="#6B5F4D" text-anchor="{anchor}">{name}</text>'

    return f'''<svg viewBox="0 0 {W} {H}" class="elev-svg">
      {labels}
      {segments}
      <text x="{PAD_L}" y="9" font-size="8" fill="#6B5F4D">高低差プロファイル(芝) 最大{max_h:.1f}m</text>
    </svg>
    <p class="elev-legend">
      <span><i style="background:#B0433A"></i>上り</span>
      <span><i style="background:#1E5FA8"></i>下り</span>
      <span><i style="background:#8B8378"></i>ほぼ平坦</span>
    </p>'''


def _bias_label(inner, mid, outer):
    """枠番別複勝率から、内外どちらが有利かの一言ラベルを作る"""
    vals = [v for v in (inner, mid, outer) if v is not None]
    if len(vals) < 3:
        return "—"
    diff = outer - inner
    if diff >= 5:
        return "外枠有利"
    if diff >= 2:
        return "やや外有利"
    if diff <= -5:
        return "内枠有利"
    if diff <= -2:
        return "やや内有利"
    return "枠差小"


def _style_label(nige, senko, sashi, oikomi):
    """脚質別複勝率から傾向ラベルを作る。
    逃げ馬は「1レースに1頭だけ」なので複勝率の絶対値がどのコースでも高く出る。
    そのまま最大値を取ると全コース『逃げ中心』になって区別がつかないため、
    前(逃げ・先行)と後ろ(差し・追込)の比で相対的に判定する。"""
    if None in (nige, senko, sashi, oikomi):
        return "—"
    front = (nige + senko) / 2
    back = (sashi + oikomi) / 2
    if front <= 0:
        return "—"
    ratio = back / front
    if ratio >= 0.75:
        return "差し・追込も決まる"
    if ratio >= 0.55:
        return "前有利だが差しも届く"
    if ratio >= 0.40:
        return "前有利"
    return "前が圧倒的に有利"


def distance_table(code):
    """その競馬場の全距離ぶんの実データ集計表を作る"""
    entries = [v for v in COURSE_STATS.values() if v["track_code"] == code]
    if not entries:
        return ""
    # 芝 -> ダートの順、距離昇順
    entries.sort(key=lambda e: (0 if e["surface"] == "芝" else 1, e["distance"]))

    rows = ""
    for e in entries:
        bias = _bias_label(e["inner_top3_pct"], e["mid_top3_pct"], e["outer_top3_pct"])
        style = _style_label(e["nige_top3_pct"], e["senko_top3_pct"], e["sashi_top3_pct"], e["oikomi_top3_pct"])
        surf_cls = "surf-turf" if e["surface"] == "芝" else "surf-dirt"
        bias_cls = ""
        if "外枠有利" in bias:
            bias_cls = "bias-out"
        elif "内枠有利" in bias:
            bias_cls = "bias-in"
        rows += f"""
        <tr>
          <td><span class="surf {surf_cls}">{e['surface']}</span>{e['distance']}m</td>
          <td class="num">{e['avg_field_size']}</td>
          <td class="num">{e['fav_win_pct']}%</td>
          <td class="num {bias_cls}">{bias}</td>
          <td class="num">{e['inner_top3_pct']}/{e['mid_top3_pct']}/{e['outer_top3_pct']}</td>
          <td>{style}</td>
          <td class="num">{e['nige_top3_pct']}/{e['senko_top3_pct']}/{e['sashi_top3_pct']}/{e['oikomi_top3_pct']}</td>
          <td class="num small">{e['n_races']}</td>
        </tr>"""

    return f"""
      <details class="dist-details">
        <summary>距離別データ({len(entries)}コース) ▾</summary>
        <div class="dist-scroll">
        <table class="dist-table">
          <thead>
            <tr>
              <th>コース</th><th>平均<br>頭数</th><th>1人気<br>勝率</th><th>枠傾向</th>
              <th>複勝率<br>内/中/外</th><th>脚質傾向</th><th>複勝率<br>逃/先/差/追</th><th>R数</th>
            </tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>
        </div>
        <p class="dist-note">2015年以降の実レース結果から集計。脚質は4コーナー通過順位を頭数で正規化して分類。
        数値はすべて複勝率(3着内率)。</p>
      </details>"""


JRA_COURSE_URLS = {
    "01": "https://www.jra.go.jp/facilities/race/sapporo/course/index.html",
    "02": "https://www.jra.go.jp/facilities/race/hakodate/course/index.html",
    "03": "https://www.jra.go.jp/facilities/race/fukushima/course/index.html",
    "04": "https://www.jra.go.jp/facilities/race/niigata/course/index.html",
    "05": "https://www.jra.go.jp/facilities/race/tokyo/course/index.html",
    "06": "https://www.jra.go.jp/facilities/race/nakayama/course/index.html",
    "07": "https://www.jra.go.jp/facilities/race/chukyo/course/index.html",
    "08": "https://www.jra.go.jp/facilities/race/kyoto/course/index.html",
    "09": "https://www.jra.go.jp/facilities/race/hanshin/course/index.html",
    "10": "https://www.jra.go.jp/facilities/race/kokura/course/",
}


def track_card(code, c):
    svg = track_svg(code, c)
    url = JRA_COURSE_URLS.get(code, "https://www.jra.go.jp/facilities/race/")
    return f'''
    <article class="course-card">
      <h2>{c['name']}競馬場</h2>
      {svg}
      {elevation_svg(code, c)}
      <table class="course-table">
        <tr><th>芝 1周</th><td>{c['turf_lap']}</td></tr>
        <tr><th>芝 直線</th><td>{c['turf_straight']}</td></tr>
        <tr><th>芝 高低差</th><td>{c['elevation']}</td></tr>
        <tr><th>ダート 1周</th><td>{c['dirt_lap']}</td></tr>
        <tr><th>ダート 直線</th><td>{c['dirt_straight']}</td></tr>
      </table>
      <p class="course-feature"><strong>特徴:</strong> {c['feature']}</p>
      <p class="course-tendency"><strong>傾向:</strong> {c['tendency']}</p>
      {distance_table(code)}
      <p class="course-official"><a href="{url}" target="_blank" rel="noopener">JRA公式の正確なコース図を見る →</a></p>
    </article>'''


cards_html = "".join(track_card(code, c) for code, c in COURSES.items())

html = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>JRA競馬場ガイド</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Shippori+Mincho:wght@600;800&family=Noto+Sans+JP:wght@400;500;700&display=swap" rel="stylesheet">
<style>
  :root {{
    --turf: #1F4D3A; --paper: #F6F1E4; --paper-line: #E4DAC0;
    --ink: #2A2118; --ink-soft: #6B5F4D; --gold: #B8862B;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--paper); color: var(--ink); font-family: "Noto Sans JP", sans-serif; padding-bottom: 48px; }}
  h1, h2 {{ font-family: "Shippori Mincho", serif; }}
  header.top {{ background: var(--turf); color: #fff; padding: 20px; }}
  header.top .inner {{ max-width: 900px; margin: 0 auto; }}
  header.top h1 {{ margin: 0 0 4px; font-size: 1.4rem; font-weight: 800; }}
  header.top p {{ margin: 0; font-size: .8rem; color: #CFE3D8; }}
  header.top a {{ color: #F0D896; text-decoration: none; font-size: .82rem; }}
  main {{ max-width: 900px; margin: 0 auto; padding: 20px; display: grid; grid-template-columns: 1fr; gap: 16px; }}
  @media (min-width: 720px) {{ main {{ grid-template-columns: 1fr 1fr; }} }}
  .course-card {{ background: #fff; border: 1px solid var(--paper-line); border-radius: 6px; padding: 16px; }}
  .course-card h2 {{ margin: 0 0 8px; font-size: 1.15rem; }}
  .course-svg {{ width: 100%; height: auto; margin-bottom: 10px; }}
  table.course-table {{ width: 100%; border-collapse: collapse; font-size: .82rem; margin-bottom: 10px; }}
  table.course-table th {{ text-align: left; color: var(--ink-soft); font-weight: 500; padding: 4px 6px; width: 90px; }}
  table.course-table td {{ padding: 4px 6px; border-bottom: 1px solid var(--paper-line); }}
  .course-feature, .course-tendency {{ font-size: .82rem; line-height: 1.7; margin: 6px 0; }}
  .course-feature strong, .course-tendency strong {{ color: var(--gold); }}
  .course-legend {{ display: flex; gap: 14px; font-size: .74rem; color: var(--ink-soft); margin: -4px 0 10px; }}
  .course-legend i {{ display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 4px; vertical-align: middle; }}
  .elev-svg {{ width: 100%; height: auto; margin: 4px 0 2px; }}
  .elev-legend {{ display: flex; gap: 12px; font-size: .7rem; color: var(--ink-soft); margin: 0 0 10px; }}
  .elev-legend i {{ display: inline-block; width: 9px; height: 9px; border-radius: 2px; margin-right: 4px; vertical-align: middle; }}
  .course-official {{ font-size: .78rem; margin-top: 10px; }}
  .course-official a {{ color: var(--turf); font-weight: 700; text-decoration: none; }}

  .dist-details {{ margin-top: 12px; border-top: 1px solid var(--paper-line); padding-top: 10px; }}
  .dist-details summary {{ cursor: pointer; font-size: .82rem; font-weight: 700; color: var(--turf); }}
  .dist-scroll {{ overflow-x: auto; margin-top: 10px; }}
  table.dist-table {{ width: 100%; border-collapse: collapse; font-size: .72rem; min-width: 560px; }}
  table.dist-table th {{
    text-align: left; color: var(--ink-soft); font-weight: 500; padding: 5px 6px;
    border-bottom: 1px solid var(--paper-line); white-space: nowrap; font-size: .68rem; line-height: 1.3;
  }}
  table.dist-table td {{ padding: 5px 6px; border-bottom: 1px solid var(--paper-line); white-space: nowrap; }}
  table.dist-table .num {{ font-variant-numeric: tabular-nums; }}
  table.dist-table .small {{ color: var(--ink-soft); }}
  .surf {{
    display: inline-block; min-width: 20px; text-align: center; border-radius: 3px;
    font-size: .66rem; font-weight: 700; margin-right: 5px; padding: 1px 4px;
  }}
  .surf-turf {{ background: #E4F1E7; color: #1F4D3A; }}
  .surf-dirt {{ background: #F7E9D8; color: #A8631E; }}
  .bias-out {{ color: #B0433A; font-weight: 700; }}
  .bias-in {{ color: #1E5FA8; font-weight: 700; }}
  .dist-note {{ font-size: .68rem; color: var(--ink-soft); line-height: 1.6; margin: 8px 0 0; }}
  footer {{ max-width: 900px; margin: 8px auto 0; padding: 0 20px; color: var(--ink-soft); font-size: .72rem; line-height: 1.6; }}
</style>
</head>
<body>
<header class="top">
  <div class="inner">
    <h1>JRA競馬場ガイド</h1>
    <p><a href="index.html">← 予想ダッシュボードに戻る</a></p>
  </div>
</header>
<main>{cards_html}</main>
<footer>
  数値・コース構成はJRA公式サイトの「コース紹介」などの一次情報を基にした要約です。
  コース図は芝(緑)・ダート(橙)の位置関係や回り・内外コースの有無といった構造的な特徴を踏まえた
  独自の簡略図であり、発走地点ごとの正確な位置・距離は反映していません。正確なコース図は各カード内の
  リンクからJRA公式サイトをご参照ください。
</footer>
</body>
</html>"""

with open("docs/courses.html", "w", encoding="utf-8") as f:
    f.write(html)
print("docs/courses.html を生成しました(10競馬場分)")
