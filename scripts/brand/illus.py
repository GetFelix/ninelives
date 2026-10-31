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
