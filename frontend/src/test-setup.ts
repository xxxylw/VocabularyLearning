import '@testing-library/jest-dom/vitest';
import { beforeEach } from 'vitest';

// v2 cloud auth: session tokens live in localStorage. Keep tests
// isolated from whichever token a previous test file stashed — without
// this, api.test.ts's exact-fetch-shape assertions would see a stray
// Authorization header.
beforeEach(() => {
  window.localStorage.clear();
});
