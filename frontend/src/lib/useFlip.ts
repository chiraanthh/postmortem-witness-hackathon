import { useLayoutEffect, useRef } from "react";

/**
 * FLIP animation (First, Last, Invert, Play) with zero dependencies.
 *
 * Attach the returned ref to a container. Any descendant carrying a
 * `data-flip-id` attribute is tracked across renders: when it changes DOM
 * position (e.g. a hypothesis card moves from OPEN to CONFIRMED) it is first
 * snapped back to its old coordinates with no transition, then transitioned to
 * its new coordinates — so the card visibly slides between columns instead of
 * teleporting.
 *
 * `signature` should change whenever the tracked set or its ordering changes.
 */
export function useFlip<T extends HTMLElement>(signature: string) {
  const containerRef = useRef<T | null>(null);
  const prevRects = useRef<Map<string, DOMRect>>(new Map());

  useLayoutEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const nodes = container.querySelectorAll<HTMLElement>("[data-flip-id]");
    const nextRects = new Map<string, DOMRect>();

    nodes.forEach((node) => {
      const id = node.dataset.flipId;
      if (!id) return;
      const rect = node.getBoundingClientRect();
      nextRects.set(id, rect);

      const prev = prevRects.current.get(id);
      if (!prev) return;

      const dx = prev.left - rect.left;
      const dy = prev.top - rect.top;
      if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return;

      // Invert: jump back to where it was, instantly.
      node.style.transition = "none";
      node.style.transform = `translate(${dx}px, ${dy}px)`;
      // Force the browser to acknowledge the inverted position.
      void node.offsetWidth;

      // Play: release to the new position with an eased transition.
      requestAnimationFrame(() => {
        node.style.transition =
          "transform 520ms cubic-bezier(0.22, 0.61, 0.36, 1)";
        node.style.transform = "";
      });
    });

    prevRects.current = nextRects;
  }, [signature]);

  return containerRef;
}
