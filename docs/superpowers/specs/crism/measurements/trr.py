# ruff: noqa
# Phase 0 measurement script (scratch quality, kept for reproducibility). See README.md.
import numpy as np, glob
D='/Users/phillipsm/Documents/Software/varda-demo-data/data-for-paper/'
C=D+'ATO0004EC45/CRISM_CDR/'
def rd(path, L, B, S=640):
    raw=np.fromfile(path,dtype='<f4',count=L*B*S).reshape(L,B,S)
    rows=np.fromfile(path,dtype='>u2',offset=L*B*S*4,count=B)&0x1FF
    return raw, rows
cube,rows=rd(D+'FRS00031442/trdr/frs00031442_01_if168l_trr3.img',190,438)
print('rows first/last',rows[:3],rows[-3:], 'monotonic', np.all(np.diff(rows.astype(int))<0) or np.all(np.diff(rows.astype(int))>0))
print('65535 frac',np.mean(cube==65535), 'nan', np.isnan(cube).mean(), 'min/max valid', np.nanmin(np.where(cube==65535,np.nan,cube)), np.nanmax(np.where(cube==65535,np.nan,cube)))
wa,wrows=rd(C+'wa/cdr490947778566_wa0000000l_3.img',1,438)
wa=wa[0] # (438,640)
print('WA rows equal', np.array_equal(rows,wrows))
good=(wa!=65535)
bandok=good.mean(1)>0.5; print('WA bands valid',bandok.sum()); gcols=np.nonzero(good[bandok].all(0))[0]; print('valid WA columns', gcols.min(), gcols.max(), len(gcols))
# smile: wavelength variation across columns near 2 µm
b2=np.nanargmin(abs(np.nanmedian(np.where(good,wa,np.nan)[:,gcols],1)-2007))
w=wa[b2,gcols]; print('band nearest 2007nm',b2,'col range of wl (nm):',w.min(),w.max(),'p2-p98',np.percentile(w,[2,98]))
# per-band spacing
med=np.where(bandok,np.median(np.where(good,wa,np.nan)[:,gcols],1),np.nan); print('wl range',np.nanmin(med),np.nanmax(med),'spacing',np.nanmedian(np.abs(np.diff(med))))
np.save('frs1442_wa.npy',wa); np.save('frs1442_rows.npy',rows)
# valid scene columns in cube
v=(cube!=65535)&np.isfinite(cube)
colvalid=v[:,b2,:].mean(0); sc=np.nonzero(colvalid>0.9)[0]; print('scene columns', sc.min(), sc.max(), len(sc))
# noise: horizontal shift difference per band (MAD-based)
d=np.diff(np.where(v,cube,np.nan)[:,:,sc],axis=2)
sig=1.4826*np.nanmedian(np.abs(d-np.nanmedian(d,axis=(0,2),keepdims=True)),axis=(0,2))/np.sqrt(2)
lvl=np.nanmedian(np.where(v,cube,np.nan)[:,:,sc],axis=(0,2))
sel=(med>1000)&(med<2600)
for wl in (1100,1300,1500,1800,2007,2300,2500,2600):
    i=np.nanargmin(abs(med-wl)); print(f'{med[i]:.0f}nm I/F {lvl[i]:.4f} sigma_shiftdiff {sig[i]:.5f} SNR {lvl[i]/sig[i]:.0f}')
np.save('frs1442_sig.npy',sig); np.save('frs1442_lvl.npy',lvl)
