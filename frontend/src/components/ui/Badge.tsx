import type { ComponentProps } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "./cn";

const badgeVariants = cva("ui-badge", {
  variants: {
    tone: {
      neutral: "ui-badge-neutral",
      primary: "ui-badge-primary",
      success: "ui-badge-success",
      warning: "ui-badge-warning",
      danger: "ui-badge-danger",
      info: "ui-badge-info",
    },
    shape: {
      pill: "ui-badge-pill",
      tag: "ui-badge-tag",
    },
  },
  defaultVariants: {
    tone: "neutral",
    shape: "tag",
  },
});

export function Badge({
  className,
  tone,
  shape,
  ...props
}: ComponentProps<"span"> & VariantProps<typeof badgeVariants>) {
  return (
    <span
      data-slot="badge"
      className={cn(badgeVariants({ tone, shape }), className)}
      {...props}
    />
  );
}
