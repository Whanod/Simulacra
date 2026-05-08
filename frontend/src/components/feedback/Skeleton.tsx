interface SkeletonProps {
  width?: string | number;
  height?: string | number;
  rounded?: boolean;
  className?: string;
}

export default function Skeleton({
  width = "100%",
  height = "1rem",
  rounded = true,
  className,
}: SkeletonProps) {
  const style: React.CSSProperties = {
    width: typeof width === "number" ? `${width}px` : width,
    height: typeof height === "number" ? `${height}px` : height,
    borderRadius: rounded ? "4px" : 0,
    background:
      "linear-gradient(90deg, rgba(255,255,255,0.04) 0%, rgba(255,255,255,0.10) 50%, rgba(255,255,255,0.04) 100%)",
    backgroundSize: "200% 100%",
    animation: "skeleton-pulse 1.4s ease-in-out infinite",
  };
  return (
    <>
      <div className={className} style={style} aria-hidden="true" />
      <style>{`@keyframes skeleton-pulse { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }`}</style>
    </>
  );
}
