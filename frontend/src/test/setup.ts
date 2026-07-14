import { cleanup } from "@testing-library/react";
import { afterEach, beforeAll, vi } from "vitest";
import "@testing-library/jest-dom/vitest";

class ResizeObserverMock {
  callback: ResizeObserverCallback;

  constructor(callback: ResizeObserverCallback) {
    this.callback = callback;
  }

  observe(target: Element) {
    this.callback([
      {
        target,
        contentRect: {
          width: 960,
          height: 560,
          x: 0,
          y: 0,
          top: 0,
          left: 0,
          right: 960,
          bottom: 560,
          toJSON: () => ({}),
        },
      } as ResizeObserverEntry,
    ], this as unknown as ResizeObserver);
  }

  unobserve() {}
  disconnect() {}
}

beforeAll(() => {
  vi.stubGlobal("ResizeObserver", ResizeObserverMock);
  Object.defineProperty(HTMLElement.prototype, "requestFullscreen", {
    configurable: true,
    value: vi.fn().mockResolvedValue(undefined),
  });
  Object.defineProperty(document, "exitFullscreen", {
    configurable: true,
    value: vi.fn().mockResolvedValue(undefined),
  });
  vi.stubGlobal("matchMedia", vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })));
});

afterEach(() => {
  cleanup();
});
