# ruff: noqa
# Phase 0 measurement script (scratch quality, kept for reproducibility). See README.md.
import numpy as np, math
D='/Users/phillipsm/Documents/Software/varda-demo-data/data-for-paper/FRT00005850/mtrdr/'
L,S=1043,990
de=np.memmap(D+'frt00005850_07_de167l_mtr1.img',dtype='<f4',mode='r',shape=(14,L,S))
lat=np.array(de[3]);lon=np.array(de[4])
R=3394839.813316312;k=math.cos(math.radians(15))
x=R*k*np.radians(lon); y=R*np.radians(lat)
ok=(lat!=65535)&(lat>-90)
# fit x = a + 18*col  ; y = b - 18*row
rows,cols=np.nonzero(ok)
xa=x[ok]-18*cols; yb=y[ok]+18*rows
print('x at col0 center: median',np.median(xa),'std',xa.std(),' y at row0 center median',np.median(yb),'std',yb.std())
print('ENVI tie x 4417887.6 y 1091944.9 -> center would be', 4417887.6+9, 1091944.9-9)
