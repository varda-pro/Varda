# ruff: noqa
# Phase 0 measurement script (scratch quality, kept for reproducibility). See README.md.
import numpy as np, math
a,b=3396190.0,3376200.0
for lat in (15,):
    for kind in ('centric','graphic'):
        p=math.radians(lat)
        if kind=='centric':
            r=a*b/math.sqrt((b*math.cos(p))**2+(a*math.sin(p))**2)
        else:
            # radius of curvature N? and geocentric radius at geodetic lat
            e2=1-(b/a)**2; N=a/math.sqrt(1-e2*math.sin(p)**2)
            r=N
        print(kind, r)
R=3394839.813316312
print('m/deg',2*math.pi*R/360, 'res', 2*math.pi*R/360/18)
D='/Users/phillipsm/Documents/Software/varda-demo-data/data-for-paper/'
import glob
for h in sorted(glob.glob(D+'*/mtrdr/*_if*_mtr3.hdr')):
    t=open(h).read()
    import re
    mi=re.search(r'map info = \{([^}]*)\}',t).group(1).split(',')
    sph=re.search(r'SPHEROID\["([^"]+)",([0-9.]+)',t)
    sp=re.search(r'Standard_Parallel_1",([-0-9.]+)',t).group(1)
    x,y,ps=float(mi[3]),float(mi[4]),float(mi[5])
    print(h.split('/')[-1], sph.group(1), sph.group(2), 'sp',sp, 'x/ps',x/ps,'y/ps',y/ps, ps)
