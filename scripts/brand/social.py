import os, build, word
from mark import mark
W,H=1280,640
def social():
    body,w,h=build.horizontal("dark")
    sc=760/w; x=(W-w*sc)/2; y=150
    paths,dots,xe=word.word([("A distributed operating system built on the Felix log",build.REG,"#93aebb")],30,0,0,-0.005)
    tx=(W-xe)/2
    paths,_,_=word.word([("A distributed operating system built on the Felix log",build.REG,"#93aebb")],30,tx,y+h*sc+70,-0.005)
    dots=word.word([("A distributed operating system built on the Felix log",build.REG,"#93aebb")],30,tx,y+h*sc+70,-0.005)[1]
    paths+= [f'<circle cx="{a:.1f}" cy="{b:.1f}" r="{r:.1f}" fill="#93aebb"/>' for a,b,r in dots]
    # a faint log along the bottom edge
    # keep everything 40 px inside the edges, the area GitHub never crops
    M=40; n=27; pitch=44; rw=38
    sx=M+(W-2*M-(n*pitch-(pitch-rw)))/2; sy=H-M-14
    strip="".join(f'<rect x="{sx+i*pitch:.1f}" y="{sy}" width="{rw}" height="14" rx="3" fill="#12a3c4" opacity="{0.10 if i!=17 else 0.03}"/>' for i in range(n-1))
    strip+=f'<rect x="{sx+(n-1)*pitch:.1f}" y="{sy}" width="{rw}" height="14" rx="3" fill="#f5a623" opacity=".55"/>'
    bg=('<defs><radialGradient id="sbg" cx=".5" cy=".42" r=".75"><stop offset="0" stop-color="#14384a"/>'
        '<stop offset="1" stop-color="#0b1a24"/></radialGradient></defs>'
        f'<g><rect width="{W}" height="{H}" fill="url(#sbg)"/>{strip}'
        f'<g transform="translate({x:.1f},{y}) scale({sc:.4f})">'+"".join(body)+'</g>'+"".join(paths)+'</g>')
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 640" width="1280" height="640">{bg}</svg>'
if __name__=="__main__":
    open(os.path.join(build.OUT,"social-preview.svg"),"w").write(social())
