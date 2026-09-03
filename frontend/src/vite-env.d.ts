/// <reference types="vite/client" />

// Minimal ambient declaration so component tests can read repo files (e.g.
// asserting design tokens in styles.css) without adding @types/node as a
// dependency — node:fs is available at runtime since vitest runs on Node.
declare module 'node:fs' {
  export function readFileSync(path: string | URL, encoding: string): string;
}
