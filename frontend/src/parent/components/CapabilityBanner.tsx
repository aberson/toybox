import type { JSX } from "react";

export interface CapabilityBannerProps {
  reason: string | null;
}

export function CapabilityBanner(props: CapabilityBannerProps): JSX.Element | null {
  const { reason } = props;
  if (reason === null || reason === "") return null;
  return (
    <div
      role="alert"
      data-testid="capability-banner"
      style={{
        padding: "8px 16px",
        background: "#fff3cd",
        borderBottom: "1px solid #ffeeba",
        color: "#664d03",
        fontSize: 13,
      }}
    >
      Claude is not currently capable: {reason}
    </div>
  );
}
