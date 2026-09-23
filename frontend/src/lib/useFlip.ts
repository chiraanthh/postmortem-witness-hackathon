import { useLayoutEffect, useRef } from "react";

/**
 * FLIP animation (First, Last, Invert, Play) with zero dependencies.
 * Clears leftover transforms on cleanup so a mid-animation re-render cannot
 * leave a card translated out of its column (empty Ruled Out with count 01).
 */
export function useFlip<T extends HTMLElement>(signature: string) {
  const containerRef = useRef<T | null>(null);
  const prevRects = useRef<Map<string, DOMRect>>(new Map());

  useLayoutEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const reduce =
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    const nodes = container.querySelectorAll<HTMLElement>("[data-flip-id]");
    const nextRects = new Map<string, DOMRect>();
    const pending: HTMLElement[] = [];

    nodes.forEach((node) => {
      const id = node.dataset.flipId;
      if (!id) return;
      // Clear any stuck transform before measuring.
      node.style.transition = "none";
      node.style.transform = "";
      const rect = node.getBoundingClientRect();
      nextRects.set(id, rect);

      if (reduce) return;

      const prev = prevRects.current.get(id);
      if (!prev) return;

      const dx = prev.left - rect.left;
      const dy = prev.top - rect.top;
      if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return;

      node.style.transform = `translate(${dx}px, ${dy}px)`;
      pending.push(node);
      void node.offsetWidth;
    });

    let raf = 0;
    if (pending.length > 0) {
      raf = requestAnimationFrame(() => {
        pending.forEach((node) => {
          node.style.transition =
            "transform 520ms cubic-bezier(0.22, 0.61, 0.36, 1)";
          node.style.transform = "";
        });
      });
    }

    prevRects.current = nextRects;
    return () => {
      if (raf) cancelAnimationFrame(raf);
      nodes.forEach((node) => {
        node.style.transition = "";
        node.style.transform = "";
      });
    };
  }, [signature]);

  return containerRef;
}
