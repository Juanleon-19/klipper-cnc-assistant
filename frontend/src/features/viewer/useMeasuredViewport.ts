import { useEffect, useState } from "react";

import type { ViewportSize } from "./viewerTypes";

const DEFAULT_VIEWPORT: ViewportSize = { width: 960, height: 560 };

export function useMeasuredViewport(node: HTMLDivElement | null): ViewportSize {
  const [viewport, setViewport] = useState<ViewportSize>(DEFAULT_VIEWPORT);

  useEffect(() => {
    if (!node) {
      return;
    }

    const update = (width: number, height: number) => {
      if (width < 16 || height < 16) {
        return;
      }
      setViewport({
        width: Math.floor(width),
        height: Math.floor(height),
      });
    };

    update(node.clientWidth, node.clientHeight);
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) {
        return;
      }
      update(entry.contentRect.width, entry.contentRect.height);
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [node]);

  return viewport;
}

