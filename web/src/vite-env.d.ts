/// <reference types="vite/client" />

export {};

declare module "vue-router" {
  interface RouteMeta {
    public?: boolean;
    title?: string;
  }
}
