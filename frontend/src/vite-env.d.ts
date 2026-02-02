/// <reference types="vite/client" />

import type { AppConfig } from './types';

declare global {
  interface Window {
    __APP_CONFIG__?: AppConfig;
  }
}

export {};
