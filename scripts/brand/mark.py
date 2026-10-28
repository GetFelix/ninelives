import math
def P(cx,cy,r,deg):
    a=math.radians(deg); return (cx+r*math.cos(a), cy+r*math.sin(a))
def f(p): return f"{p[0]:.2f},{p[1]:.2f}"
def cubic(p0,p1,p2,p3,t):
    u=1-t
    return tuple(u**3*a+3*u*u*t*b+3*u*t*t*c+t**3*d for a,b,c,d in zip(p0,p1,p2,p3))

CX,CY,R,W=124,100,54,28   # bowl
RO,RI=R+W/2,R-W/2

def head_path():
    # outer silhouette: circle with two ears grown out of it, plus the counter (evenodd)
    def ear(s):
        b_out=P(CX,CY,RO-2,-90+s*58); b_in=P(CX,CY,RO-2,-90+s*20)
        tip=P(CX,CY,RO+42,-90+s*38)
        def lerp(p,q,t,push):
            x=p[0]+(q[0]-p[0])*t; y=p[1]+(q[1]-p[1])*t
            dx,dy=q[0]-p[0],q[1]-p[1]; l=math.hypot(dx,dy)
            nx,ny=dy/l*s,-dx/l*s   # outward for the outer edge
            return (x+nx*push,y+ny*push)
        c1=lerp(b_out,tip,.35,4); c2=lerp(b_out,tip,.75,2.5)
        c3=lerp(tip,b_in,.3,1); c4=lerp(tip,b_in,.7,-1)
        return b_out,c1,c2,tip,c3,c4,b_in
    L=ear(-1); Rr=ear(1)
    d =f"M{f(L[0])} C{f(L[1])} {f(L[2])} {f(L[3])} C{f(L[4])} {f(L[5])} {f(L[6])} "
    d+=f"A{RO},{RO} 0 0 1 {f(Rr[6])} "
    d+=f"C{f(Rr[5])} {f(Rr[4])} {f(Rr[3])} C{f(Rr[2])} {f(Rr[1])} {f(Rr[0])} "
    d+=f"A{RO},{RO} 0 1 1 {f(L[0])} Z "
    d+=f"M{CX+RI},{CY} A{RI},{RI} 0 1 0 {CX-RI},{CY} A{RI},{RI} 0 1 0 {CX+RI},{CY} Z"
    return d

def tail_pieces(n=9, gap=2.4):
    # centreline: down the bowl's right side, sweeping left, the tip flicking up
    p0=(CX+R,CY); p1=(CX+R,CY+92); p2=(CX-20,CY+150); p3=(CX-70,CY+112)
    N=600
    pts=[cubic(p0,p1,p2,p3,i/N) for i in range(N+1)]
    d=[0]
    for a,b in zip(pts,pts[1:]): d.append(d[-1]+math.dist(a,b))
    total=d[-1]
    def width(s): # taper from W to 40%
        t=s/total; return W*(1-0.5*t**1.4)
    def normal(i):
        a=pts[max(i-1,0)]; b=pts[min(i+1,N)]
        dx,dy=b[0]-a[0],b[1]-a[1]; l=math.hypot(dx,dy); return (-dy/l,dx/l)
    def piece(s0,s1,round_end=False):
        idx=[i for i in range(N+1) if s0<=d[i]<=s1]
        left=[];right=[]
        for i in idx:
            nx,ny=normal(i); w=width(d[i])/2
            left.append((pts[i][0]+nx*w,pts[i][1]+ny*w)); right.append((pts[i][0]-nx*w,pts[i][1]-ny*w))
        path="M"+" L".join(f(p) for p in left)
        if round_end:
            w=width(d[idx[-1]])/2
            path+=f" A{w:.2f},{w:.2f} 0 0 0 {f(right[-1])}"
        path+=" L"+" L".join(f(p) for p in reversed(right))+" Z"
        return path
    start=62  # records begin where the stem leaves the bowl
    body=piece(0,start-gap)
    seg=(total-start-gap*(n-1))/n
    recs=[piece(start+i*(seg+gap), start+i*(seg+gap)+seg, round_end=(i==n-1)) for i in range(n)]
    return body,recs

def eyes(fill):
    out=[]
    for k,ex in enumerate((CX-18,CX+18)):
        ey=CY+5; tilt=10 if k==0 else -10
        out.append(f'<g transform="rotate({tilt} {ex} {ey})"><g class="lid">'
          f'<path d="M{ex-13},{ey} C{ex-7},{ey-11} {ex+7},{ey-11} {ex+13},{ey} C{ex+7},{ey+11} {ex-7},{ey+11} {ex-13},{ey} Z" fill="{fill}"/>'
          f'<path class="pupil" d="M{ex},{ey-9.5} C{ex+4.6},{ey-3} {ex+4.6},{ey+3} {ex},{ey+9.5} C{ex-4.6},{ey+3} {ex-4.6},{ey-3} {ex},{ey-9.5} Z" fill="#06121a"/>'
          f'<circle cx="{ex+4.5}" cy="{ey-3.5}" r="1.8" fill="#fff" opacity=".9"/></g></g>')
    return out

def mark(theme="light", ghost=4, animate=False):
    ink=("#0e2a3a","#0a6f8c") if theme=="light" else ("#d9f6ff","#5fd3ef")
    tail=("#12a3c4","#0b6f8f") if theme=="light" else ("#5fd3ef","#1fa8c9")
    defs=(f'<defs><linearGradient id="ink" x1="0" y1="0" x2="1" y2="1">'
          f'<stop offset="0" stop-color="{ink[0]}"/><stop offset="1" stop-color="{ink[1]}"/></linearGradient>'
          f'<linearGradient id="tail" gradientUnits="userSpaceOnUse" x1="{CX+R}" y1="{CY+20}" x2="{CX-70}" y2="{CY+130}">'
          f'<stop offset="0" stop-color="{ink[1]}"/><stop offset=".55" stop-color="{tail[0]}"/><stop offset="1" stop-color="{tail[1]}"/></linearGradient>'
          f'<radialGradient id="night" cx=".5" cy=".42" r=".6"><stop offset="0" stop-color="#14384a"/><stop offset="1" stop-color="#06121a"/></radialGradient><filter id="bloom" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="4"/></filter><radialGradient id="glow"><stop offset="0" stop-color="#ffd36a"/><stop offset="1" stop-color="#f29d12"/></radialGradient></defs>')
    o=[defs]
    body,recs=tail_pieces()
    o.append(f'<path d="{body}" fill="url(#tail)"/>')
    for i,r in enumerate(recs):
        if i==len(recs)-1: o.append(f'<path class="rec tip" d="{r}" fill="url(#glow)"/>')
        elif i==ghost: o.append(f'<path class="rec ghost" d="{r}" fill="url(#tail)" opacity=".28"/>')
        else: o.append(f'<path class="rec" style="--i:{i}" d="{r}" fill="url(#tail)"/>')
    o.append(f'<circle cx="{CX}" cy="{CY}" r="{RI+1}" fill="url(#night)"/>')
    o.append(f'<path d="{head_path()}" fill="url(#ink)" fill-rule="evenodd"/>')
    o.append(f'<circle cx="{CX}" cy="{CY}" r="{RI}" fill="none" stroke="{tail[0]}" stroke-width="1.6" opacity=".55"/>')
    o.append('<g class="bloom" filter="url(#bloom)" opacity=".55">'+''.join(f'<ellipse cx="{ex}" cy="{CY+5}" rx="13" ry="8" fill="#f5a623"/>' for ex in (CX-18,CX+18))+'</g>')
    o+=eyes("url(#glow)")
    return o

def svg(body,w=256,h=256,scale=2,bg=None,extra=""):
    b=f'<rect width="{w}" height="{h}" fill="{bg}"/>' if bg else ""
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w*scale}" height="{h*scale}">{extra}{b}'+"".join(body)+'</svg>'
