import type { ReactNode } from "react";

type IconProps = { size?: number };

function Icon({ children, size = 17 }: IconProps & { children: ReactNode }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true" xmlns="http://www.w3.org/2000/svg">
      {children}
    </svg>
  );
}

export function BriefcaseIcon(props: IconProps) {
  return <Icon {...props}><path d="M9 6V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v1m-9 0h12a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2Zm0 5h16M10 11v2m4-2v2" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></Icon>;
}

export function HomeIcon(props: IconProps) {
  return <Icon {...props}><path d="M3 11.5 12 4l9 7.5M5 10v10h14V10M9 20v-6h6v6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></Icon>;
}

export function ListIcon(props: IconProps) {
  return <Icon {...props}><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></Icon>;
}

export function CalendarIcon(props: IconProps) {
  return <Icon {...props}><path d="M7 3v3m10-3v3M4 9h16M5 5h14a1 1 0 0 1 1 1v14H4V6a1 1 0 0 1 1-1Z" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></Icon>;
}

export function PeopleIcon(props: IconProps) {
  return <Icon {...props}><path d="M16 19c0-2.2-1.8-4-4-4s-4 1.8-4 4M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm6.5 6.5c0-1.6-1-3-2.4-3.6M17 5.2a3 3 0 0 1 0 5.6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></Icon>;
}

export function GearIcon(props: IconProps) {
  return <Icon {...props}><path d="M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Zm0-12v2m0 13v2M4.2 4.2l1.4 1.4m12.8 12.8 1.4 1.4M2 12h2m16 0h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></Icon>;
}

export function PulseIcon(props: IconProps) {
  return <Icon {...props}><path d="M3 12h4l2-6 4 12 3-8 2 2h3" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></Icon>;
}

export function ClockIcon(props: IconProps) {
  return <Icon {...props}><path d="M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-13v5l3 2" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></Icon>;
}

export function XIcon(props: IconProps) {
  return <Icon {...props}><path d="M18 6 6 18M6 6l12 12" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></Icon>;
}

export function ChevronLeftIcon(props: IconProps) {
  return <Icon {...props}><path d="m15 18-6-6 6-6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></Icon>;
}

export function ChevronRightIcon(props: IconProps) {
  return <Icon {...props}><path d="m9 18 6-6-6-6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></Icon>;
}

export function SearchIcon(props: IconProps) {
  return <Icon {...props}><path d="m21 21-4.3-4.3M10.5 18a7.5 7.5 0 1 1 0-15 7.5 7.5 0 0 1 0 15Z" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></Icon>;
}

export function FilterIcon(props: IconProps) {
  return <Icon {...props}><path d="M4 5h16l-6 7v5l-4 2v-7L4 5Z" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></Icon>;
}

export function PlusIcon(props: IconProps) {
  return <Icon {...props}><path d="M12 5v14M5 12h14" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></Icon>;
}

export function ExternalIcon(props: IconProps) {
  return <Icon {...props}><path d="M14 4h6v6M13 11l7-7M20 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></Icon>;
}

export function DownloadIcon(props: IconProps) {
  return <Icon {...props}><path d="M12 3v11m0 0 4-4m-4 4-4-4M4 17v3h16v-3" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></Icon>;
}

export function ChatIcon(props: IconProps) {
  return <Icon {...props}><path d="M21 12a8 8 0 0 1-8 8H7l-4 2 1.5-4A8 8 0 1 1 21 12Z" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></Icon>;
}

export function SendIcon(props: IconProps) {
  return <Icon {...props}><path d="M22 2 11 13M22 2l-7 20-4-9-9-4 20-7Z" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></Icon>;
}
