# ruff: noqa
# Phase 0 measurement script (scratch quality, kept for reproducibility). See README.md.
import glob,re,math
D='/Users/phillipsm/Documents/Software/varda-demo-data/data-for-paper/'
for l in sorted(glob.glob(D+'*/mtrdr/*_if*_mtr3.lbl')):
    t=open(l).read(); g=lambda k: float(re.search(k+r'\s*=\s*([-0-9.]+)',t).group(1))
    R=g('A_AXIS_RADIUS')*1000; lp=g('CENTER_LATITUDE'); s=g('MAP_SCALE')*1000
    W=g('WESTERNMOST_LONGITUDE'); N=g('MAXIMUM_LATITUDE'); SO=g('SAMPLE_PROJECTION_OFFSET'); LO=g('LINE_PROJECTION_OFFSET')
    h=open(l.replace('.lbl','.hdr')).read(); mi=re.search(r'map info = \{([^}]*)\}',h).group(1).split(',')
    tx,ty=float(mi[3]),float(mi[4])
    xw=R*math.cos(math.radians(lp))*math.radians(W); yn=R*math.radians(N)
    print(l.split('/')[-1][:11], 'tx-xW',round(tx-xw,2),'ty-yN',round(ty-yn,2),'tx/s+SO',round(tx/s+SO,3),'ty/s-LO',round(ty/s-LO,3))
