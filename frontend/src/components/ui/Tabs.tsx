"use client";

interface TabItem<T extends string> {
  key: T;
  label: string;
}

interface TabsProps<T extends string> {
  items: TabItem<T>[];
  active: T;
  onChange: (key: T) => void;
}

export default function Tabs<T extends string>({ items, active, onChange }: TabsProps<T>) {
  return (
    <div className="tabs">
      {items.map(({ key, label }) => (
        <button
          key={key}
          className={active === key ? "active" : ""}
          onClick={() => onChange(key)}
        >
          {label}
        </button>
      ))}
    </div>
  );
}
