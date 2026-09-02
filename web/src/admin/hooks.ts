"use client";

import { useCallback, useEffect, useRef } from "react";

export function useStableVoidCallback(callback: () => void): () => void {
  const callbackRef = useRef(callback);
  useEffect(() => {
    callbackRef.current = callback;
  }, [callback]);
  return useCallback(() => callbackRef.current(), []);
}
