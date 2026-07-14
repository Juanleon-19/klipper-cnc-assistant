import type { PropsWithChildren } from "react";

import type { UiTone } from "../lib/ui";

type StatusBadgeProps = PropsWithChildren<{
  tone?: UiTone;
}>;

export function StatusBadge({ tone = "neutral", children }: StatusBadgeProps) {
  return <span className={`status-badge status-badge--${tone}`}>{children}</span>;
}
