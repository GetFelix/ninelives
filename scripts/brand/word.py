from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
def word(text_parts, size, x0, base, tracking=-0.01, dotr=None):
    """text_parts: list of (text, fontfile, fill). Draw outlines; 'i' becomes dotless i plus a dot we return."""
    out=[]; dots=[]; x=x0
    for text,ff,fill in text_parts:
        font=TTFont(ff); gs=font.getGlyphSet(); cmap=font.getBestCmap(); upm=font['head'].unitsPerEm
        sc=size/upm
        for ch in text:
            gname=cmap[ord(ch)]
            if ch=='i': gname=cmap[0x131]
            pen=SVGPathPen(gs)
            tp=TransformPen(pen,(sc,0,0,-sc,x,base))
            gs[gname].draw(tp)
            out.append(f'<path d="{pen.getCommands()}" fill="{fill}"/>')
            adv=gs[gname].width*sc
            if ch=='i':
                # dot centred over the stem, at Inter's i-dot height
                dots.append((x+adv/2, base-size*0.69, dotr[ff] if dotr else size*0.07))
            x+=adv+tracking*size
    return out,dots,x
