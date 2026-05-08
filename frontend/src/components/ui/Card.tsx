interface CardProps {
  title?: string;
  badge?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
  onClick?: () => void;
}

export default function Card({ title, badge, actions, children, className, style, onClick }: CardProps) {
  return (
    <div className={`card${className ? ` ${className}` : ""}`} style={style} onClick={onClick}>
      {(title || badge || actions) && (
        <div className="card-header">
          <h3>{title}</h3>
          {badge}
          {actions}
        </div>
      )}
      {children}
    </div>
  );
}
