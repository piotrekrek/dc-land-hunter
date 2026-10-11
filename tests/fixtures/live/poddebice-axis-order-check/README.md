Real responses from the EGiB WFS and ULDK, captured on 11.10.2026 with a bounding box sent in the wrong axis
order (easting first; the service expects northing first for EPSG:2180). The 20 parcels are in Poddębice gmina
(parcel 101103_5.0011.1 is at about 51.90 N, 18.78 E), not in Konin.

They are kept because they are a real response: the id field (`ID_DZIALKI`), the attribute names, the empty
fields, `numberMatched="unknown"` and the ULDK format all come from the live service. Do not use them as the
CGE Konin sample. The Konin sample is captured again with the corrected request.
