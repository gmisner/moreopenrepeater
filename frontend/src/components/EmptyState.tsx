import type { ReactNode } from "react";

/** A quiet "nothing here yet" box. Say what is missing and, if you can, what to do about it. */
export function EmptyState({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div className="mor-empty">
      <p>{children}</p>
      {action}
    </div>
  );
}
