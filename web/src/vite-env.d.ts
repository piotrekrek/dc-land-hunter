/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_BASEMAP_STYLE?: string;
  readonly VITE_BASEMAP_STYLE_LIGHT?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
