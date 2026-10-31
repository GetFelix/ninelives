"""Generates the NineLives illustrations: the style reference and the blog figures.

The drawing rules are in docs/brand/illustrations.md. Colours come from the site's --ill-* custom
properties, with light/dark fallbacks so a file also renders on its own (GitHub, an <img>).
"""
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "../..")

# Every drawing carries the same stylesheet. Inlined on the site, --ill-* comes from site.css and
# the --f-* fallbacks are never used. Classes are prefixed nl- so they can't collide with the site.
STYLE = """
.nl-ill{--f-ink:#0e2a3a;--f-soft:rgba(95,211,239,.22);--f-cat:#0e2a3a;--f-eye:#5fd3ef;--f-label:#46536a;--f-amber-text:#8f4c00}
@media (prefers-color-scheme:dark){.nl-ill{--f-ink:#5fd3ef;--f-soft:rgba(18,163,196,.16);--f-cat:#5fd3ef;--f-eye:#0e2a3a;--f-label:#a9b6c8;--f-amber-text:#f7b13a}}
.nl-k{fill:none;stroke:var(--ill-ink,var(--f-ink));stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
.nl-m{fill:var(--ill-soft,var(--f-soft));stroke:var(--ill-line,#12a3c4);stroke-width:2;stroke-linejoin:round}
.nl-o{fill:none;stroke:var(--ill-ink,var(--f-ink));stroke-width:2;stroke-linejoin:round}
.nl-ka{fill:none;stroke:var(--ill-accent,#f5a623);stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
.nl-dead{opacity:.35}
.nl-dash{stroke-dasharray:5 6}
.nl-r{fill:var(--ill-line,#12a3c4)}
.nl-new{fill:var(--ill-accent,#f5a623)}
.nl-lost{opacity:.28}
.nl-cat{fill:var(--ill-cat,var(--f-cat));stroke:var(--ill-ink,var(--f-ink));stroke-width:2;stroke-linejoin:round}
.nl-tail-e{fill:none;stroke:var(--ill-ink,var(--f-ink));stroke-width:10;stroke-linecap:round}
.nl-tail{fill:none;stroke:var(--ill-cat,var(--f-cat));stroke-width:6;stroke-linecap:round}
.nl-eye{fill:var(--ill-eye,var(--f-eye))}
.nl-eye-a{fill:var(--ill-accent,#f5a623)}
.nl-lid{fill:none;stroke:var(--ill-eye,var(--f-eye));stroke-width:2.4;stroke-linecap:round}
.nl-t{font-family:var(--mono,"IBM Plex Mono",ui-monospace,monospace);font-size:13px;fill:var(--ill-label,var(--f-label))}
.nl-ta{fill:var(--ill-accent-text,var(--f-amber-text))}
""".strip()

W, H = 720, 480  # 3:2


def svg(body, title=None, w=W, h=H):
    t = f"<title>{title}</title>" if title else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" class="nl-ill" viewBox="0 0 {w} {h}" '
            f'width="{w}" height="{h}">{t}<style>{STYLE}</style>' + "".join(body) + "</svg>\n")


def text(x, y, s, anchor="start", cls="nl-t"):
    return f'<text class="{cls}" x="{x}" y="{y}" text-anchor="{anchor}">{s}</text>'


def cat(x, y, eyes="open"):
    """The cat is the 9: a head (the bowl) and a tail (the stem). (x, y) is the head's centre."""
    tail = f"M{x+17} {y+8}C{x+23} {y+26} {x+17} {y+40} {x+3} {y+45}"
    head = (f"M{x-18} {y+4}C{x-18} {y-7} {x-15} {y-13} {x-12} {y-16}L{x-14} {y-30}L{x-3} {y-19}"
            f"Q{x} {y-20} {x+3} {y-19}L{x+14} {y-30}L{x+12} {y-16}C{x+15} {y-13} {x+18} {y-7} {x+18} {y+4}"
            f"C{x+18} {y+16} {x+10} {y+22} {x} {y+22}C{x-10} {y+22} {x-18} {y+16} {x-18} {y+4}Z")
    out = [f'<path class="nl-tail-e" d="{tail}"/>', f'<path class="nl-tail" d="{tail}"/>',
           f'<path class="nl-cat" d="{head}"/>']
    if eyes in ("open", "amber"):
        c = "nl-eye" if eyes == "open" else "nl-eye-a"
        for dx in (-7, 7):
            out.append(f'<ellipse class="{c}" cx="{x+dx}" cy="{y+3}" rx="4.2" ry="2.8"/>')
    elif eyes == "happy":
        for dx in (-7, 7):
            out.append(f'<path class="nl-lid" d="M{x+dx-4} {y+5}Q{x+dx} {y-1} {x+dx+4} {y+5}"/>')
    return out


def machine(x, y, w, h, label, dead=False):
    cls = "nl-o nl-dash" if dead else "nl-m"
    g = [f'<rect class="{cls}" x="{x}" y="{y}" width="{w}" height="{h}" rx="12"/>',
         f'<path class="nl-k{" nl-dash" if dead else ""}" d="M{x+16} {y+18}h26M{x+16} {y+28}h16"/>',
         text(x + 16, y + h - 16, label)]
    return [f'<g class="nl-dead">'] + g + ["</g>"] if dead else g


def records(x, y, n, lost=(), newest=True):
    """The log: tail segments in a row, oldest on the left. The newest is amber."""
    out = []
    for i in range(n):
        cls = "nl-new" if newest and i == n - 1 else "nl-r"
        if i in lost:
            cls += " nl-lost"
        out.append(f'<rect class="{cls}" x="{x + i*28}" y="{y}" width="22" height="14" rx="5"/>')
    return out


def arrow(p0, c1, c2, p1, cls="nl-k"):
    """A curved arrow; the head follows the curve's last tangent."""
    ang = math.atan2(p1[1] - c2[1], p1[0] - c2[0])
    head = []
    for da in (2.6, -2.6):
        head.append((p1[0] + 11 * math.cos(ang + da), p1[1] + 11 * math.sin(ang + da)))
    d = (f"M{p0[0]} {p0[1]}C{c1[0]} {c1[1]} {c2[0]} {c2[1]} {p1[0]} {p1[1]}"
         f"M{head[0][0]:.1f} {head[0][1]:.1f}L{p1[0]} {p1[1]}L{head[1][0]:.1f} {head[1][1]:.1f}")
    return [f'<path class="{cls}" d="{d}"/>']


def reference():
    b = []
    b += machine(90, 200, 200, 120, "node a", dead=True)
    b += machine(430, 200, 200, 120, "node b")
    b += ['<g class="nl-dead">', '<path class="nl-k nl-dash" d="M190 322C190 360 270 368 300 390"/>', "</g>"]
    b += ['<path class="nl-k" d="M530 322C530 360 470 368 448 390"/>']
    b += arrow((200, 168), (240, 60), (420, 60), (488, 150))
    b += records(237, 400, 9, lost=(4,))
    b += [text(222, 412, "log", "end")]
    b += cat(530, 176)
    return svg(b, "A cat leaves a dead machine, drawn faded and dashed, and steps along a curved "
                  "arrow onto a live machine. Both machines sit over one log of nine records; one "
                  "record is faint and the newest is amber.")


def three_vms():
    b = []
    for i, (label, dead) in enumerate((("vm 1 · linux", False), ("vm 2 · linux", True),
                                       ("vm 3 · linux", False))):
        x = 60 + i * 215
        b += machine(x, 190, 170, 110, label, dead=dead)
        link = f'M{x+85} 302C{x+85} 340 {360 + (i-1)*60} 346 {360 + (i-1)*70} 380'
        if dead:
            b += ['<g class="nl-dead">', f'<path class="nl-k nl-dash" d="{link}"/>', "</g>"]
        else:
            b += [f'<path class="nl-k" d="{link}"/>']
    b += arrow((330, 160), (380, 70), (500, 70), (542, 138))
    b += records(237, 390, 9)
    b += [text(222, 402, "log", "end")]
    b += cat(575, 166)
    return svg(b)


def stacks():
    b = []
    for cx, title, own in ((190, "stock linux", False), (530, "own kernel", True)):
        x0 = cx - 130
        b += [text(cx, 92, title, "middle")]
        b += cat(cx, 150, eyes="open")
        b += [f'<rect class="nl-m" x="{x0}" y="180" width="260" height="56" rx="12"/>',
              text(x0 + 16, 214, "felix + felixd")]
        b += [f'<rect class="nl-m" x="{x0}" y="250" width="260" height="140" rx="12"/>',
              text(x0 + 16, 274, "kernel")]
        cells = (("boot", "paging", "sched"), ("drivers", "storage", "network"))
        for row, names in enumerate(cells):
            for col, name in enumerate(names):
                x, y = x0 + 8 + col * 84, 288 + row * 48
                todo = own and row == 1
                cls = "nl-ka" if todo else "nl-m"
                b += [f'<rect class="{cls}" x="{x}" y="{y}" width="76" height="38" rx="8"/>',
                      text(x + 38, y + 24, name, "middle", "nl-t nl-ta" if todo else "nl-t")]
        b += [f'<rect class="nl-m" x="{x0}" y="404" width="260" height="40" rx="12"/>',
              text(x0 + 16, 429, "hardware")]
    return svg(b)


def middle_paths():
    b = ['<path class="nl-k" d="M90 300H630"/>']
    stops = (("stock linux", ""), ("rust for", "linux"), ("asterinas", "linux syscalls"),
             ("hermit", "unikernel"), ("own kernel", ""))
    for i, (a, s) in enumerate(stops):
        x = 90 + i * 135
        b += [f'<rect class="nl-r" x="{x-11}" y="293" width="22" height="14" rx="5"/>',
              text(x, 336, a, "middle")]
        if s:
            b += [text(x, 354, s, "middle")]
    b += arrow((122, 222), (220, 90), (520, 90), (622, 280))
    b += [text(372, 118, "?", "middle")]
    b += [text(90, 420, "reuse more", "start"), text(630, 420, "write more", "end")]
    b += ['<path class="nl-k" d="M190 416H530M520 410L530 416L520 422M200 410L190 416L200 422"/>']
    b += cat(90, 246, eyes="amber")
    return svg(b)


def replay_decision():
    b = []
    b += machine(70, 110, 180, 100, "model v3", dead=True)
    b += ['<g class="nl-dead">', '<path class="nl-k nl-dash" d="M160 212C160 310 379 296 379 372"/>', "</g>"]
    b += records(200, 380, 10, newest=False)
    b += ['<rect class="nl-new" x="368" y="380" width="22" height="14" rx="5"/>',
          text(379, 416, "answer", "middle", "nl-t nl-ta"), text(185, 392, "log", "end")]
    b += machine(470, 160, 190, 100, "replay")
    b += arrow((450, 372), (470, 320), (530, 300), (548, 268))
    b += cat(565, 136, eyes="happy")
    return svg(b)


def shadow_model():
    b = []
    b += records(234, 70, 9, newest=False)
    b += [text(219, 82, "log", "end")]
    b += arrow((300, 96), (260, 126), (150, 120), (130, 182))
    b += arrow((390, 96), (420, 140), (500, 140), (520, 180))
    b += machine(90, 190, 220, 100, "v3 · live")
    b += [f'<rect class="nl-o" x="410" y="190" width="220" height="100" rx="12"/>',
          '<path class="nl-k" d="M426 208h26M426 218h16"/>', text(426, 274, "v4 · shadow")]
    b += cat(256, 166)
    b += records(130, 340, 5, newest=False)
    b += records(450, 340, 5, newest=False)
    b += ['<rect class="nl-new" x="534" y="340" width="22" height="14" rx="5"/>',
          text(545, 376, "first diff", "middle", "nl-t nl-ta")]
    b += [text(200, 420, "acted on", "middle"), text(520, 420, "recorded only", "middle")]
    return svg(b)



# ---------------------------------------------------------------------------------------------
# Animated drawings. Each moving part is one CSS keyframe track on a shared loop of T seconds,
# so parts can't drift apart and the loop has one seam. Only transform and opacity animate.

class Loop:
    EASE = {"lin": "linear", "in": "ease-in", "out": "ease-out",
            "back": "cubic-bezier(.3,1.6,.6,1)", "step": "steps(1,end)"}
    IO = "cubic-bezier(.45,0,.55,1)"  # the default between keys

    def __init__(self, prefix, length, start=0.0, still=0.0):
        self.prefix, self.T, self.start, self.still = prefix, length, start, still
        self.tracks = []

    def track(self, name, keys, prop="transform"):
        """keys: (t, value, easing to the next key). A transform value is a dict of x, y, r, sx, sy."""
        keys = sorted((k for k in keys if 0 <= k[0] <= self.T), key=lambda k: k[0])
        if keys[0][0] > 0:
            keys.insert(0, (0, keys[0][1], "lin"))
        if keys[-1][0] < self.T:
            keys.append((self.T, keys[-1][1], "lin"))
        self.tracks.append((f"{self.prefix}-{name}", prop, keys))
        return f"{self.prefix}-{name}"

    @staticmethod
    def _n(v):
        return f"{v:.2f}".rstrip("0").rstrip(".").replace("0.", ".", 1) if abs(v) < 1 else f"{v:.2f}".rstrip("0").rstrip(".")

    def _transforms(self, vals):
        # Every key of a track gets the same function list, so they interpolate part by part.
        get = lambda v, k, d: v.get(k, d)
        use_t = any(get(v, "x", 0) or get(v, "y", 0) for v in vals)
        use_r = any(get(v, "r", 0) for v in vals)
        use_s = any(get(v, "sx", 1) != 1 or get(v, "sy", 1) != 1 for v in vals)
        out = []
        for v in vals:
            p = []
            if use_t:
                p.append("translate(%s,%s)" % tuple(f"{self._n(get(v, k, 0))}px" if get(v, k, 0) else "0" for k in "xy"))
            if use_r:
                p.append(f"rotate({self._n(get(v, 'r', 0))}deg)" if get(v, "r", 0) else "rotate(0)")
            if use_s:
                p.append(f"scale({self._n(get(v, 'sx', 1))},{self._n(get(v, 'sy', 1))})")
            out.append(" ".join(p) or "none")
        return out

    def css(self, scope):
        sel = f'{scope} [class*="{self.prefix}-"]'
        # !important: the site turns every animation off under reduced motion, and this drawing
        # keeps its tracks so it can hold one chosen frame instead.
        out = [f"{sel}{{transform-origin:0 0;animation-duration:{self.T:g}s!important;"
               f"animation-iteration-count:infinite!important;animation-fill-mode:both!important;"
               f"animation-delay:-{self.start:g}s!important;animation-timing-function:{self.IO}!important}}",
               f"@media (prefers-reduced-motion:reduce){{{sel}{{animation-play-state:paused!important;"
               f"animation-delay:-{self.still:g}s!important}}}}"]
        for name, prop, keys in self.tracks:
            vals = [v for _, v, _ in keys]
            if prop == "transform":
                vals = self._transforms(vals)
            rules, order = {}, []
            for (t, _, e), v in zip(keys, vals):
                if (v, e) not in rules:
                    rules[(v, e)] = []
                    order.append((v, e))
                rules[(v, e)].append(f"{t / self.T * 100:.2f}".rstrip("0").rstrip(".") + "%")
            frames = "".join(
                f"{','.join(rules[k])}{{{prop}:{k[0]}"
                + ("" if k[1] == "io" else f";animation-timing-function:{self.EASE[k[1]]}") + "}"
                for k in order)
            out.append(f"@keyframes {name}{{{frames}}}.{name}{{animation-name:{name}!important}}")
        return "\n".join(out)


def part(cls, pivot, inner):
    """An animated part: a static move to its pivot, the animated group, the content moved back."""
    px, py = pivot
    return (f'<g transform="translate({px:g} {py:g})"><g class="{cls}">'
            f'<g transform="translate({-px:g} {-py:g})">{inner}</g></g></g>')


def lost_page():
    """The 404: the cat looks for the page on one machine after another. Each machine dies under
    it and the cat fails over to the next, a life gone each time. All nine spent, it shrugs and
    the lives replay, because it's a log."""
    k = 1.45                                   # the cat's size against the reference drawing
    xs = (140, 360, 580)                       # machine centres
    top, mh, mw = 274, 104, 176                # machine roof, height, width
    cy = top - 22 * k                          # the cat's head centre when it sits on a roof
    durs = [1.4, 1.34, 1.28, 1.22, 1.16, 1.1, 1.04, 0.98, 0.92]   # the search speeds up a little
    starts = [sum(durs[:i]) for i in range(9)]
    end = sum(durs)
    T = round(end + 2.7, 2)
    peer_at = lambda i: starts[i] + 0.36 * durs[i]
    loop = Loop("nl-a", T, start=0.25, still=round(peer_at(4) + 0.05, 2))
    tr = loop.track
    on = lambda i: i % 3                       # the machine the cat is on during search i

    # Machines die under the cat and come back a little later.
    machines = []
    for m, x in enumerate(xs):
        deaths = [starts[i] + 0.48 * durs[i] for i in range(9) if on(i) == m]
        live, dead = [], []
        for d in deaths:
            back = d + 1.3
            live += [(d, "1", "lin"), (d + 0.18, "0", "lin"), (back, "0", "lin"), (back + 0.3, "1", "lin")]
            dead += [(d, "0", "lin"), (d + 0.18, "1", "lin"), (back, "1", "lin"), (back + 0.3, "0", "lin")]
        if deaths[-1] + 1.6 > T:  # the last one is back by the loop's seam
            live[-2:] = [(T - 0.5, "0", "lin"), (T - 0.2, "1", "lin")]
            dead[-2:] = [(T - 0.5, "1", "lin"), (T - 0.2, "0", "lin")]
        cl = tr(f"live{m}", [(0, "1", "lin")] + live, "opacity")
        cd = tr(f"dead{m}", [(0, "0", "lin")] + dead, "opacity")
        x0, y0 = x - mw / 2, top
        slot = f'<rect class="nl-o" x="{x0 + 104}" y="{y0 + 46}" width="44" height="22" rx="7"/>'
        label = text(x0 + 16, y0 + mh - 16, f"node {'abc'[m]}")
        machines += [f'<g class="{cl}">',
                     f'<rect class="nl-m" x="{x0}" y="{y0}" width="{mw}" height="{mh}" rx="12"/>',
                     f'<path class="nl-k" d="M{x0 + 16} {y0 + 18}h26M{x0 + 16} {y0 + 28}h16"/>',
                     slot, label, "</g>",
                     f'<g class="{cd}"><g class="nl-dead">',
                     f'<rect class="nl-o nl-dash" x="{x0}" y="{y0}" width="{mw}" height="{mh}" rx="12"/>',
                     f'<path class="nl-k nl-dash" d="M{x0 + 16} {y0 + 18}h26M{x0 + 16} {y0 + 28}h16"/>',
                     label, "</g></g>"]

    # The failover arrows, one per pair, shown while the cat makes that hop.
    arrows = []
    for a, b in ((0, 1), (1, 2), (2, 0)):
        hops = [i for i in range(9) if on(i) == a]
        keys = [(0, "0", "lin")]
        for i in hops:
            t0, t1 = starts[i] + 0.5 * durs[i], starts[i] + durs[i]
            keys += [(t0, "0", "out"), (t0 + 0.15, "1", "lin"), (t1 + 0.1, "1", "in"), (t1 + 0.4, "0", "lin")]
        cls = tr(f"arrow{a}", keys, "opacity")
        x0, x1 = xs[a] + 8, xs[b] - 8
        h = 110 if abs(b - a) == 1 else 170
        sgn = 1 if x1 > x0 else -1
        p0, p1 = (x0 + 10 * sgn, cy - 34), (x1 - 10 * sgn, cy - 34)
        c1, c2 = (x0 + 40 * sgn, cy - 34 - h), (x1 - 40 * sgn, cy - 34 - h)
        arrows += [f'<g class="{cls}">'] + arrow(p0, c1, c2, p1) + ["</g>"]

    # Where the cat is: x moves evenly through each hop, height is its own track.
    pos, hop, sq, tilt, look, wide, ear_l, ear_r, tail = ([] for _ in range(9))
    for i in range(9):
        s, d = starts[i], durs[i]
        x_from, x_to = xs[on(i)], xs[on(i + 1)]
        up, land = s + 0.7 * d, s + d
        high = 170 if abs(x_to - x_from) > 300 else 110
        pos += [(up, {"x": x_from}, "lin"), (land, {"x": x_to}, "lin")]
        hop += [(up, {}, "out"), ((up + land) / 2, {"y": -high}, "in"), (land, {}, "lin")]
        # Anticipation, stretch, squash on landing, settle.
        sq += [(s + 0.56 * d, {}, "io"), (up, {"sx": 1.12, "sy": 0.84}, "out"),
               (up + 0.08, {"sx": 0.92, "sy": 1.1}, "io"), (land - 0.03, {"sx": 0.97, "sy": 1.04}, "in"),
               (land + 0.04, {"sx": 1.16, "sy": 0.82}, "out"), (land + 0.16, {"sx": 0.97, "sy": 1.04}, "io"),
               (land + 0.28, {}, "io")]
        # Peer at the slot, then the startle when the machine goes.
        pk, dk = peer_at(i), s + 0.48 * d
        tilt += [(s + 0.12 * d, {}, "io"), (pk, {"r": 19, "x": 7}, "io"), (dk, {"r": 17, "x": 7}, "out"),
                 (dk + 0.08, {"r": -6}, "io"), (up, {"r": -4}, "io"), (land, {"r": 6}, "io"), (land + 0.25, {}, "io")]
        look += [(s + 0.1 * d, {"x": 1}, "io"), (pk, {"x": 2.4, "y": 1.9}, "io"), (dk, {"x": 2.4, "y": 1.9}, "out"),
                 (dk + 0.06, {"x": (1.8 if x_to > x_from else -1.8), "y": -0.6}, "io"), (land, {}, "io")]
        wide += [(dk, {}, "out"), (dk + 0.06, {"sx": 1.12, "sy": 1.32}, "io"), (up, {"sx": 1.05, "sy": 1.12}, "io"),
                 (land + 0.1, {}, "io")]
        ear_l += [(dk, {}, "out"), (dk + 0.07, {"r": -14}, "io"), (dk + 0.25, {}, "io"),
                  (land, {}, "out"), (land + 0.06, {"r": 12}, "io"), (land + 0.22, {}, "io")]
        ear_r += [(dk + 0.03, {}, "out"), (dk + 0.1, {"r": 14}, "io"), (dk + 0.28, {}, "io"),
                  (land, {}, "out"), (land + 0.08, {"r": -10}, "io"), (land + 0.24, {}, "io")]
        tail += [(up, {}, "io"), (up + 0.12, {"r": -24}, "io"), (land + 0.05, {"r": 18}, "io"),
                 (land + 0.22, {"r": -8}, "io"), (land + 0.4, {}, "io")]
    # The shrug: nine lives spent. Look at the counter, tilt one way and the other, ears out.
    f = end
    tilt += [(f + 0.3, {}, "io"), (f + 0.55, {"r": -10}, "io"), (f + 0.9, {"r": -10}, "io"),
             (f + 1.15, {"r": 9}, "io"), (f + 1.5, {"r": 9}, "io"), (f + 1.85, {}, "io")]
    look += [(f + 0.2, {}, "io"), (f + 0.35, {"x": -1.6, "y": 1.8}, "io"), (f + 0.75, {"x": -1.6, "y": 1.8}, "io"),
             (f + 0.9, {}, "io")]
    ear_l += [(f + 0.45, {}, "io"), (f + 0.65, {"r": -24}, "io"), (f + 1.6, {"r": -24}, "back"), (f + 1.9, {}, "io")]
    ear_r += [(f + 0.45, {}, "io"), (f + 0.65, {"r": 24}, "io"), (f + 1.6, {"r": 24}, "back"), (f + 1.9, {}, "io")]
    tail += [(f + 0.5, {}, "io"), (f + 0.9, {"r": -14}, "io"), (f + 1.3, {"r": 10}, "io"), (f + 1.75, {}, "io")]
    sq += [(f + 0.45, {}, "io"), (f + 0.65, {"sx": 1.04, "sy": 0.94}, "io"), (f + 1.6, {"sx": 1.04, "sy": 0.94}, "io"),
           (f + 1.85, {}, "io")]
    pos += [(T, {"x": xs[0]}, "lin")]
    c_pos, c_hop, c_sq, c_tilt = tr("pos", pos), tr("hop", hop), tr("sq", sq), tr("tilt", tilt)
    c_look, c_wide = tr("look", look), tr("wide", wide)
    c_el, c_er, c_tail = tr("earl", ear_l), tr("earr", ear_r), tr("tail", tail)
    blinks = [starts[1] + 0.25, starts[5] + 0.2, f + 2.25]
    bl = []
    for b in blinks:
        bl += [(b, {}, "in"), (b + 0.07, {"sy": 0.1}, "out"), (b + 0.19, {}, "io")]
    c_blink = tr("blink", bl)
    c_open = tr("open", [(0, "1", "step"), (f + 0.6, "0", "step"), (f + 1.75, "1", "step")], "opacity")
    c_happy = tr("happy", [(0, "0", "step"), (f + 0.6, "1", "step"), (f + 1.75, "0", "step")], "opacity")

    # The lives: one goes faint at each death, and all nine replay in order at the end.
    pips = []
    for i in range(9):
        d = starts[i] + 0.48 * durs[i]
        back = f + 1.0 + i * 0.09
        c_o = tr(f"life{i}", [(0, "1", "lin"), (d, "1", "out"), (d + 0.25, ".28", "lin"),
                              (back, ".28", "lin"), (back + 0.12, "1", "lin")], "opacity")
        c_s = tr(f"lifes{i}", [(d, {}, "out"), (d + 0.12, {"sx": 0.7, "sy": 0.7}, "io"), (d + 0.3, {}, "lin"),
                               (back - 0.01, {}, "step"), (back, {"sx": 0.6, "sy": 0.6}, "back"),
                               (back + 0.26, {}, "lin")])
        x = 214 + i * 34
        pips.append(part(c_s, (x + 12, 440), f'<g class="{c_o}"><rect class="nl-r" x="{x}" y="433" width="24" height="14" rx="5"/></g>'))
    c_lives = tr("lives", [(0, "1", "lin"), (f + 0.8, "1", "lin"), (f + 0.95, "0", "lin"), (T - 0.45, "0", "lin"),
                           (T - 0.3, "1", "lin")], "opacity")
    c_replay = tr("replay", [(0, "0", "lin"), (f + 0.8, "0", "lin"), (f + 0.95, "1", "lin"), (T - 0.45, "1", "lin"),
                             (T - 0.3, "0", "lin")], "opacity")
    counter = (pips + [f'<g class="{c_lives}">', text(200, 444, "lives", "end"), "</g>",
                       f'<g class="{c_replay}">', text(200, 444, "replay", "end"), "</g>"])

    # The cat, drawn at size k around its head centre. Ears and tail are their own parts.
    def P(*pts):
        return " ".join(f"{a * k:g}" if isinstance(a, (int, float)) else a for a in pts)
    head = ("M" + P(-18, 4) + "C" + P(-18, -7, -15, -13, -12, -16) + "Q" + P(0, -21, 12, -16)
            + "C" + P(15, -13, 18, -7, 18, 4) + "C" + P(18, 16, 10, 22, 0, 22) + "C" + P(-10, 22, -18, 16, -18, 4) + "Z")
    ear_lp = "M" + P(-16, -8) + "L" + P(-14, -30) + "L" + P(-1, -16) + "Z"
    ear_rp = "M" + P(16, -8) + "L" + P(14, -30) + "L" + P(1, -16) + "Z"
    tail_p = "M" + P(16, 8) + "C" + P(23, 26, 17, 40, 3, 45)
    eyes = "".join(f'<ellipse class="nl-eye-a" cx="{dx * k:g}" cy="{3 * k:g}" rx="{4.2 * k:g}" ry="{2.8 * k:g}"/>' for dx in (-7, 7))
    happy = "".join(f'<path class="nl-lid nl-happy" d="M{(dx - 4) * k:g} {5 * k:g}Q{dx * k:g} {-1 * k:g} {(dx + 4) * k:g} {5 * k:g}"/>' for dx in (-7, 7))
    wt = 6 * k  # tail body; its outline stays 2px, as the brand's strokes do
    cat_parts = (
        part(c_tail, (16 * k, 8 * k),
             f'<path class="nl-tail-e" style="stroke-width:{wt + 4:g}px" d="{tail_p}"/>'
             f'<path class="nl-tail" style="stroke-width:{wt:g}px" d="{tail_p}"/>')
        + part(c_el, (-9 * k, -15 * k), f'<path class="nl-cat" d="{ear_lp}"/>')
        + part(c_er, (9 * k, -15 * k), f'<path class="nl-cat" d="{ear_rp}"/>')
        + f'<path class="nl-cat" d="{head}"/>'
        + part(c_blink, (0, 3 * k), f'<g class="{c_open}">' + part(c_wide, (0, 3 * k), f'<g class="{c_look}">{eyes}</g>') + "</g>")
        + f'<g class="{c_happy}">{happy}</g>'
    )
    bottom = (0, 22 * k)
    cat = (f'<g transform="translate(0 {cy:g})"><g class="{c_pos}"><g class="{c_hop}">'
           + part(c_sq, bottom, part(c_tilt, bottom, cat_parts)) + "</g></g></g>")

    body = arrows + machines + counter + [cat]
    title = ("The NineLives cat looks for the page on node a, b and c in turn. Each machine dies under "
             "it and the cat fails over to the next, losing one of nine lives each time. With none left "
             "it shrugs, and the nine lives replay.")
    css = loop.css(".nl-ill")
    out = svg(body, title)
    return out.replace("</style>", "\n" + css + "</style>", 1)


if __name__ == "__main__":
    def write(rel, s):
        p = os.path.join(ROOT, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").write(s)

    ref = reference()
    write("docs/brand/illustration-reference.svg", ref)
    write("site/static/brand/illustration-reference.svg", ref)
    post = "site/static/img/blog/linux-or-our-own-kernel/"
    write(post + "1-three-vms.svg", three_vms())
    write(post + "2-two-stacks.svg", stacks())
    write(post + "3-middle-paths.svg", middle_paths())
    post = "site/static/img/blog/what-if-every-decision-could-be-replayed/"
    write(post + "1-replay-decision.svg", replay_decision())
    write(post + "2-shadow-model.svg", shadow_model())
    write("site/static/img/404.svg", lost_page())
