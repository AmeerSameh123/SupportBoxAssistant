import { useMemo } from "react";
import { toast as sonner } from "sonner";

export function useToasts() {
  const toast = useMemo(
    () => ({
      success: (title: string, detail?: string) =>
        sonner.success(title, { description: detail, duration: 6_000 }),
      error: (title: string, detail?: string) =>
        sonner.error(title, { description: detail, duration: 14_000 }),
      warn: (title: string, detail?: string) =>
        sonner.warning(title, { description: detail, duration: 10_000 }),
      info: (title: string, detail?: string) =>
        sonner.info(title, { description: detail, duration: 6_000 }),
    }),
    [],
  );

  return { toast };
}
