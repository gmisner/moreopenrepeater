export function BrandMark({ size = 32 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      aria-hidden="true"
      style={{ color: "var(--mantine-color-accent-4)", flex: "none" }}
    >
      <g fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
        <path d="M16 14 11.6 27.5M16 14l4.4 13.5M13.2 22.6h5.6" />
        <path d="M20.2 6.8a6 6 0 0 1 0 8.4M11.8 6.8a6 6 0 0 0 0 8.4" />
        <path d="M23.4 3.6a10.5 10.5 0 0 1 0 14.8M8.6 3.6a10.5 10.5 0 0 0 0 14.8" opacity=".55" />
      </g>
    </svg>
  );
}
