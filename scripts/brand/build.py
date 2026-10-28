import mark as gen2, word
from mark import mark, svg
import os
HERE=os.path.dirname(os.path.abspath(__file__))
B=os.path.join(HERE,"font/extras/otf/")
OUT=os.path.join(HERE,"../../docs/brand")
BOLD=B+"Inter-Bold.otf"; REG=B+"Inter-Regular.otf"
def colors(theme):
    return ("#0e2a3a","#0b86a6") if theme=="light" else ("#e6f7fd","#5fd3ef")
def wordmark(theme,size,x0,base):
    ink,acc=colors(theme)
    paths,dots,xend=word.word([("nine",BOLD,ink),("lives",REG,acc)],size,x0,base,-0.025,
                              dotr={BOLD:size*0.074,REG:size*0.058})
    for (x,y,r) in dots: paths.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="url(#glow)"/>')
    return paths,xend
def horizontal(theme):
    p,xend=wordmark(theme,104,236,166)
    return mark(theme)+p, int(xend+20), 256
def stacked(theme):
    # measure first, then centre the word under the mark
    _,xe=wordmark(theme,78,0,0); ww=xe
    W=max(int(ww+40),300); off=(W-ww)/2
    p,_=wordmark(theme,78,off,330)
    m=[f'<g transform="translate({(W-256)/2+8},0)">']+mark(theme)+['</g>']
    return m+p, W, 356
if __name__=="__main__":
    for name,fn in (("horizontal",horizontal),("stacked",stacked)):
        for theme in ("light","dark"):
            body,w,h=fn(theme)
            open(os.path.join(OUT,f"{name}-{theme}.svg"),"w").write(svg(body,w=w,h=h,scale=1))
