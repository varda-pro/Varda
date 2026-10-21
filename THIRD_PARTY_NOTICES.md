# Third-party notices

Varda is released under the GNU General Public License v3 or later. These
notices cover material in Varda that comes from elsewhere.

## CRISM processing (`src/varda/crism/`)

- **jezero-rim-mapping** (MIT License, © Valantinas,
  <https://github.com/euchas/jezero-rim-mapping>): the idea of masking slew
  rows from the along-track ground step, re-implemented in
  `varda.crism.readers.usableLines`. The MIT License text is at the
  project's repository.
- **PDS data tables** in `src/varda/crism/resources/`: the MTRDR wavelength
  table (`mtrdr_wavelengths.csv`) and the table of atmospheric-transmission
  CDRs (`at_cdrs.csv`). Both are public data from NASA's Planetary Data
  System (MRO CRISM archive, <https://pds-geosciences.wustl.edu/>).
- **Learned artifact bases** (`resources/artifacts/*.npz`): derived by Varda
  from PDS CRISM TRR3 observations FRS00031442, FRS0002FE75 and
  FRS00031260 (public PDS data) and the PDS atmospheric-transmission CDRs.

CAT's auxiliary files are never included in Varda. If you point Varda to
them, they are read from your own copy.
