import os, build
from mark import mark, svg
CSS="""<style>
.lid{transform-box:fill-box;transform-origin:center;animation:blink 6s infinite}
.rec{animation:pulse 3.6s ease-in-out infinite;animation-delay:calc(var(--i) * 140ms)}
.tip{animation:tip 3.6s ease-in-out infinite;animation-delay:1.15s}
.ghost{animation:ghost 7.2s ease-in-out infinite}
.bloom{animation:bloom 3.6s ease-in-out infinite}
@keyframes blink{0%,46%,50%,100%{transform:scaleY(1)}48%{transform:scaleY(.08)}}
@keyframes pulse{0%,30%,100%{filter:none}12%{filter:brightness(1.45) saturate(1.2)}}
@keyframes tip{0%,30%,100%{filter:none}12%{filter:brightness(1.25) drop-shadow(0 0 5px #ffb52e)}}
@keyframes ghost{0%,40%{opacity:.28}55%,85%{opacity:1}100%{opacity:.28}}
@keyframes bloom{0%,100%{opacity:.45}50%{opacity:.75}}
@media (prefers-reduced-motion:reduce){.lid,.rec,.tip,.ghost,.bloom{animation:none}}
</style>"""
if __name__=="__main__":
  for theme in ("light","dark"):
    open(os.path.join(build.OUT,f"mark-animated-{theme}.svg"),"w").write(svg(mark(theme),scale=1,extra=CSS))
    open(os.path.join(build.OUT,f"mark-{theme}.svg"),"w").write(svg(mark(theme),scale=1))
