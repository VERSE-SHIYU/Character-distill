const Svg = ({ size = 18, children, style, ...props }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" width={size} height={size} style={{ verticalAlign: 'middle', ...style }} {...props}>
    {children}
  </svg>
)

export const Globe = (props) => (
  <Svg {...props}>
    <circle cx="12" cy="12" r="10" />
    <path d="M2 12h20" />
    <path d="M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10 15.3 15.3 0 014-10z" />
  </Svg>
)

export const Speaker = (props) => (
  <Svg {...props}>
    <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
    <path d="M19.07 4.93a10 10 0 010 14.14" />
    <path d="M15.54 8.46a5 5 0 010 7.07" />
  </Svg>
)

export const SpeakerOff = (props) => (
  <Svg {...props}>
    <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
    <line x1="23" y1="9" x2="17" y2="15" />
    <line x1="17" y1="9" x2="23" y2="15" />
  </Svg>
)

export const RefreshCw = (props) => (
  <Svg {...props}>
    <polyline points="23 4 23 10 17 10" />
    <path d="M20.49 15a9 9 0 11-2.12-9.36L23 10" />
  </Svg>
)

export const User = (props) => (
  <Svg {...props}>
    <path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2" />
    <circle cx="12" cy="7" r="4" />
  </Svg>
)

export const Eye = (props) => (
  <Svg {...props}>
    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
    <circle cx="12" cy="12" r="3" />
  </Svg>
)

export const EyeOff = (props) => (
  <Svg {...props}>
    <path d="M17.94 17.94A10.07 10.07 0 0112 20c-7 0-11-8-11-8a18.45 18.45 0 015.06-5.94" />
    <path d="M9.9 4.24A9.12 9.12 0 0112 4c7 0 11 8 11 8a18.5 18.5 0 01-2.16 3.19" />
    <line x1="1" y1="1" x2="23" y2="23" />
  </Svg>
)

export const AlertTriangle = (props) => (
  <Svg {...props}>
    <path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
    <line x1="12" y1="9" x2="12" y2="13" />
    <line x1="12" y1="17" x2="12.01" y2="17" />
  </Svg>
)

export const FontDecrease = (props) => (
  <Svg {...props}>
    <path d="M7 20l4-12 4 12" />
    <path d="M5 15h12" />
    <line x1="19" y1="8" x2="23" y2="8" />
  </Svg>
)

export const FontIncrease = (props) => (
  <Svg {...props}>
    <path d="M7 20l4-12 4 12" />
    <path d="M5 15h12" />
    <line x1="19" y1="6" x2="23" y2="6" />
    <line x1="21" y1="4" x2="21" y2="8" />
  </Svg>
)

export const Search = (props) => (
  <Svg {...props}>
    <circle cx="11" cy="11" r="8" />
    <line x1="21" y1="21" x2="16.65" y2="16.65" />
  </Svg>
)

export const Heart = (props) => (
  <Svg {...props}>
    <path d="M20.84 4.61a5.5 5.5 0 00-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 00-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 000-7.78z" />
  </Svg>
)

export const HeartOff = (props) => (
  <Svg {...props}>
    <path d="M20.84 4.61a5.5 5.5 0 00-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 00-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 000-7.78z" />
    <line x1="2.5" y1="2.5" x2="21.5" y2="21.5" />
  </Svg>
)

export const Smile = (props) => (
  <Svg {...props}>
    <circle cx="12" cy="12" r="10" />
    <path d="M8 14s1.5 2 4 2 4-2 4-2" />
    <line x1="9" y1="9" x2="9.01" y2="9" />
    <line x1="15" y1="9" x2="15.01" y2="9" />
  </Svg>
)

export const Shield = (props) => (
  <Svg {...props}>
    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
  </Svg>
)

export const Star = (props) => (
  <Svg {...props}>
    <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
  </Svg>
)

export const Book = (props) => (
  <Svg {...props}>
    <path d="M4 19.5A2.5 2.5 0 016.5 17H20" />
    <path d="M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z" />
  </Svg>
)

export const MessageSquare = (props) => (
  <Svg {...props}>
    <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z" />
  </Svg>
)

export const Handshake = (props) => (
  <Svg {...props}>
    <path d="M20.42 4.58a5.4 5.4 0 00-7.65 0l-.77.78-.77-.78a5.4 5.4 0 00-7.65 0 5.4 5.4 0 000 7.65L12 18.47l7.65-7.65a5.4 5.4 0 000-7.24z" />
    <path d="M7.5 11L12 15l4.5-4" />
  </Svg>
)

export const Theater = (props) => (
  <Svg {...props}>
    <path d="M12 2a10 10 0 1010 10 10 10 0 00-10-10z" />
    <path d="M8.5 10a1 1 0 100-2 1 1 0 000 2z" />
    <path d="M15.5 10a1 1 0 100-2 1 1 0 000 2z" />
    <path d="M12 16c-2.5 0-4-1.5-4-1.5" />
  </Svg>
)

export const Mic = (props) => (
  <Svg {...props}>
    <path d="M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3z" />
    <path d="M19 10v2a7 7 0 01-14 0v-2" />
    <line x1="12" y1="19" x2="12" y2="23" />
    <line x1="8" y1="23" x2="16" y2="23" />
  </Svg>
)

export const Lock = (props) => (
  <Svg {...props}>
    <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
    <path d="M7 11V7a5 5 0 0110 0v4" />
  </Svg>
)

export const Mail = (props) => (
  <Svg {...props}>
    <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z" />
    <polyline points="22,6 12,13 2,6" />
  </Svg>
)

export const Edit = (props) => (
  <Svg {...props}>
    <path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7" />
    <path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z" />
  </Svg>
)

export const Trash2 = (props) => (
  <Svg {...props}>
    <polyline points="3 6 5 6 21 6" />
    <path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2" />
  </Svg>
)

export const Clipboard = (props) => (
  <Svg {...props}>
    <path d="M16 4h2a2 2 0 012 2v14a2 2 0 01-2 2H6a2 2 0 01-2-2V6a2 2 0 012-2h2" />
    <rect x="8" y="2" width="8" height="4" rx="1" ry="1" />
  </Svg>
)

export const Sprout = (props) => (
  <Svg {...props}>
    <path d="M7 20h10" />
    <path d="M10 20c0-4 2-8 2-8s2 4 2 8" />
    <path d="M12 12a6 6 0 016-6 5.5 5.5 0 00-4.5 2" />
    <path d="M12 12a6 6 0 00-6-6 5.5 5.5 0 014.5 2" />
  </Svg>
)

export const CornerUpLeft = (props) => (
  <Svg {...props}>
    <polyline points="9 14 4 9 9 4" />
    <path d="M20 20v-7a4 4 0 00-4-4H4" />
  </Svg>
)

export const Home = (props) => (
  <Svg {...props}>
    <path d="M3 9l9-7 9 7v11a2 2 0 01-2 2H5a2 2 0 01-2-2z" />
    <polyline points="9 22 9 12 15 12 15 22" />
  </Svg>
)

export const Sparkles = (props) => (
  <Svg {...props}>
    <path d="M12 3l1.5 4.5L18 9l-4.5 1.5L12 15l-1.5-4.5L6 9l4.5-1.5z" />
    <path d="M5 16l1 2 2 1-2 1-1 2-1-2-2-1 2-1z" />
    <path d="M17 16l1 2 2 1-2 1-1 2-1-2-2-1 2-1z" />
  </Svg>
)

export const Folder = (props) => (
  <Svg {...props}>
    <path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z" />
  </Svg>
)

export const File = (props) => (
  <Svg {...props}>
    <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" />
    <polyline points="14 2 14 8 20 8" />
    <line x1="16" y1="13" x2="8" y2="13" />
    <line x1="16" y1="17" x2="8" y2="17" />
  </Svg>
)

export const Zap = (props) => (
  <Svg {...props}>
    <polygon points="13 2 3 14 12 14 11 22 21 10 12 10" />
  </Svg>
)

export const Music = (props) => (
  <Svg {...props}>
    <path d="M9 18V5l12-2v13" />
    <circle cx="6" cy="18" r="3" />
    <circle cx="18" cy="16" r="3" />
  </Svg>
)

export const Pin = (props) => (
  <Svg {...props}>
    <path d="M12 2a8 8 0 00-8 8c0 5.25 8 13 8 13s8-7.75 8-13a8 8 0 00-8-8z" />
    <circle cx="12" cy="10" r="3" />
  </Svg>
)

export const Tag = (props) => (
  <Svg {...props}>
    <path d="M20.59 13.41l-7.17 7.17a2 2 0 01-2.83 0L2 12V2h10l8.59 8.59a2 2 0 010 2.82z" />
    <line x1="7" y1="7" x2="7.01" y2="7" />
  </Svg>
)

export const Bookmark = (props) => (
  <Svg {...props}>
    <path d="M19 21l-7-5-7 5V5a2 2 0 012-2h10a2 2 0 012 2z" />
  </Svg>
)

export const Dashboard = (props) => (
  <Svg {...props}>
    <rect x="3" y="3" width="7" height="7" rx="1" />
    <rect x="14" y="3" width="7" height="7" rx="1" />
    <rect x="3" y="14" width="7" height="7" rx="1" />
    <rect x="14" y="14" width="7" height="7" rx="1" />
  </Svg>
)

export const Users = (props) => (
  <Svg {...props}>
    <path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2" />
    <circle cx="9" cy="7" r="4" />
    <path d="M23 21v-2a4 4 0 00-3-3.87" />
    <path d="M16 3.13a4 4 0 010 7.75" />
  </Svg>
)

export const Ticket = (props) => (
  <Svg {...props}>
    <path d="M2 9a3 3 0 010 6v2a2 2 0 002 2h16a2 2 0 002-2v-2a3 3 0 010-6V7a2 2 0 00-2-2H4a2 2 0 00-2 2z" />
    <circle cx="12" cy="12" r="1" />
  </Svg>
)

export const BarChart = (props) => (
  <Svg {...props}>
    <line x1="12" y1="20" x2="12" y2="10" />
    <line x1="18" y1="20" x2="18" y2="4" />
    <line x1="6" y1="20" x2="6" y2="16" />
  </Svg>
)

export const Flag = (props) => (
  <Svg {...props}>
    <path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z" />
    <line x1="4" y1="22" x2="4" y2="15" />
  </Svg>
)

export const Terminal = (props) => (
  <Svg {...props}>
    <polyline points="4 17 10 11 4 5" />
    <line x1="12" y1="19" x2="20" y2="19" />
  </Svg>
)

export const Megaphone = (props) => (
  <Svg {...props}>
    <path d="M3 11l18-5v12l-18-5a2 2 0 010-4z" />
    <path d="M11.6 16.3a3 3 0 11-5.2-3" />
  </Svg>
)

export const Settings = (props) => (
  <Svg {...props}>
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-2 2 2 2 0 01-2-2v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83 0 2 2 0 010-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 01-2-2 2 2 0 012-2h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 010-2.83 2 2 0 012.83 0l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 012-2 2 2 0 012 2v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 0 2 2 0 010 2.83l-.06.06A1.65 1.65 0 0019.32 9a1.65 1.65 0 001.51 1H21a2 2 0 012 2 2 2 0 01-2 2h-.09a1.65 1.65 0 00-1.51 1z" />
  </Svg>
)

export const Download = (props) => (
  <Svg {...props}>
    <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" />
    <polyline points="7 10 12 15 17 10" />
    <line x1="12" y1="15" x2="12" y2="3" />
  </Svg>
)

export const Sun = (props) => (
  <Svg {...props}>
    <circle cx="12" cy="12" r="5" />
    <line x1="12" y1="1" x2="12" y2="3" />
    <line x1="12" y1="21" x2="12" y2="23" />
    <line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
    <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" />
    <line x1="1" y1="12" x2="3" y2="12" />
    <line x1="21" y1="12" x2="23" y2="12" />
    <line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
    <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
  </Svg>
)

export const Moon = (props) => (
  <Svg {...props}>
    <path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z" />
  </Svg>
)

export const Clock = (props) => (
  <Svg {...props}>
    <circle cx="12" cy="12" r="10" />
    <polyline points="12 6 12 12 16 14" />
  </Svg>
)

export const Palette = (props) => (
  <Svg {...props}>
    <circle cx="12" cy="12" r="10" />
    <path d="M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10 15.3 15.3 0 014-10z" />
  </Svg>
)

export const Check = (props) => (
  <Svg {...props}>
    <polyline points="20 6 9 17 4 12" />
  </Svg>
)

export const Close = (props) => (
  <Svg {...props}>
    <line x1="18" y1="6" x2="6" y2="18" />
    <line x1="6" y1="6" x2="18" y2="18" />
  </Svg>
)

export const Camera = (props) => (
  <Svg {...props}>
    <path d="M23 19a2 2 0 01-2 2H3a2 2 0 01-2-2V8a2 2 0 012-2h4l2-3h6l2 3h4a2 2 0 012 2z" />
    <circle cx="12" cy="13" r="4" />
  </Svg>
)

export const Inbox = (props) => (
  <Svg {...props}>
    <polyline points="22 12 16 12 14 15 10 15 8 12 2 12" />
    <path d="M5.45 5.11L2 12v6a2 2 0 002 2h16a2 2 0 002-2v-6l-3.45-6.89A2 2 0 0016.76 4H7.24a2 2 0 00-1.79 1.11z" />
  </Svg>
)

export const ExternalLink = (props) => (
  <Svg {...props}>
    <path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6" />
    <polyline points="15 3 21 3 21 9" />
    <line x1="10" y1="14" x2="21" y2="3" />
  </Svg>
)

export const Wifi = (props) => (
  <Svg {...props}>
    <path d="M5 12.55a11 11 0 0114.08 0" />
    <path d="M1.42 9a16 16 0 0121.16 0" />
    <path d="M8.53 16.11a6 6 0 016.95 0" />
    <line x1="12" y1="20" x2="12.01" y2="20" />
  </Svg>
)

export const Share = (props) => (
  <Svg {...props}>
    <path d="M4 12v8a2 2 0 002 2h12a2 2 0 002-2v-8" />
    <polyline points="16 6 12 2 8 6" />
    <line x1="12" y1="2" x2="12" y2="15" />
  </Svg>
)

export const ArrowLeft = (props) => (
  <Svg {...props}>
    <line x1="19" y1="12" x2="5" y2="12" />
    <polyline points="12 19 5 12 12 5" />
  </Svg>
)

export const ChevronLeft = (props) => (
  <Svg {...props}>
    <polyline points="15 18 9 12 15 6" />
  </Svg>
)

export const ChevronRight = (props) => (
  <Svg {...props}>
    <polyline points="9 18 15 12 9 6" />
  </Svg>
)

export const Lightbulb = (props) => (
  <Svg {...props}>
    <path d="M9 18h6" />
    <path d="M10 22h4" />
    <path d="M15.09 14c.18-.98.65-1.74 1.41-2.5A4.65 4.65 0 0018 8 6 6 0 006 8c0 1 .23 2.23 1.5 3.5A4.61 4.61 0 018.91 14" />
  </Svg>
)

export const List = (props) => (
  <Svg {...props}>
    <line x1="8" y1="6" x2="21" y2="6" />
    <line x1="8" y1="12" x2="21" y2="12" />
    <line x1="8" y1="18" x2="21" y2="18" />
    <line x1="3" y1="6" x2="3.01" y2="6" />
    <line x1="3" y1="12" x2="3.01" y2="12" />
    <line x1="3" y1="18" x2="3.01" y2="18" />
  </Svg>
)

export const MoreHorizontal = (props) => (
  <Svg {...props}>
    <circle cx="12" cy="12" r="1" />
    <circle cx="19" cy="12" r="1" />
    <circle cx="5" cy="12" r="1" />
  </Svg>
)

export const MoreVertical = (props) => (
  <Svg {...props}>
    <circle cx="12" cy="12" r="1" />
    <circle cx="12" cy="5" r="1" />
    <circle cx="12" cy="19" r="1" />
  </Svg>
)

export const MapPin = (props) => (
  <Svg {...props}>
    <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0118 0z" />
    <circle cx="12" cy="10" r="3" />
  </Svg>
)

export const Upload = (props) => (
  <Svg {...props}>
    <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" />
    <polyline points="17 8 12 3 7 8" />
    <line x1="12" y1="3" x2="12" y2="15" />
  </Svg>
)

export const Filter = (props) => (
  <Svg {...props}>
    <polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3" />
  </Svg>
)

export const Play = (props) => (
  <Svg {...props}>
    <polygon points="5 3 19 12 5 21 5 3" />
  </Svg>
)

export const Square = (props) => (
  <Svg {...props}>
    <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
  </Svg>
)

export const Plus = (props) => (
  <Svg {...props}>
    <line x1="12" y1="5" x2="12" y2="19" />
    <line x1="5" y1="12" x2="19" y2="12" />
  </Svg>
)

export const AlertCircle = (props) => (
  <Svg {...props}>
    <circle cx="12" cy="12" r="10" />
    <line x1="12" y1="8" x2="12" y2="12" />
    <line x1="12" y1="16" x2="12.01" y2="16" />
  </Svg>
)

export const ChevronDown = (props) => (
  <Svg {...props}>
    <polyline points="6 9 12 15 18 9" />
  </Svg>
)

export const UserPlus = (props) => (
  <Svg {...props}>
    <path d="M16 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2" />
    <circle cx="8.5" cy="7" r="4" />
    <line x1="20" y1="8" x2="20" y2="14" />
    <line x1="23" y1="11" x2="17" y2="11" />
  </Svg>
)

export const Edit3 = (props) => (
  <Svg {...props}>
    <path d="M12 20h9" />
    <path d="M16.5 3.5a2.121 2.121 0 013 3L7 19l-4 1 1-4L16.5 3.5z" />
  </Svg>
)

export const LogIn = (props) => (
  <Svg {...props}>
    <path d="M15 3h4a2 2 0 012 2v14a2 2 0 01-2 2h-4" />
    <polyline points="10 17 15 12 10 7" />
    <line x1="15" y1="12" x2="3" y2="12" />
  </Svg>
)

export const MessageCircle = (props) => (
  <Svg {...props}>
    <path d="M21 11.5a8.38 8.38 0 01-.9 3.8 8.5 8.5 0 01-7.6 4.7 8.38 8.38 0 01-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 01-.9-3.8 8.5 8.5 0 014.7-7.6 8.38 8.38 0 013.8-.9h.5a8.48 8.48 0 018 8v.5z" />
  </Svg>
)

export const AlignLeft = (props) => (
  <Svg {...props}>
    <line x1="17" y1="10" x2="3" y2="10" />
    <line x1="21" y1="6" x2="3" y2="6" />
    <line x1="21" y1="14" x2="3" y2="14" />
    <line x1="17" y1="18" x2="3" y2="18" />
  </Svg>
)

export const AlignCenter = (props) => (
  <Svg {...props}>
    <line x1="18" y1="10" x2="6" y2="10" />
    <line x1="21" y1="6" x2="3" y2="6" />
    <line x1="21" y1="14" x2="3" y2="14" />
    <line x1="18" y1="18" x2="6" y2="18" />
  </Svg>
)

export const AlignRight = (props) => (
  <Svg {...props}>
    <line x1="21" y1="10" x2="7" y2="10" />
    <line x1="21" y1="6" x2="3" y2="6" />
    <line x1="21" y1="14" x2="3" y2="14" />
    <line x1="21" y1="18" x2="7" y2="18" />
  </Svg>
)

const ICON_MAP = {
  mail: Mail,
  settings: Settings,
  palette: Palette,
  globe: Globe,
  clock: Clock,
  heart: Heart,
  user: User,
  mic: Mic,
  trash: Trash2,
  shield: Shield,
  dashboard: Dashboard,
  terminal: Terminal,
  megaphone: Megaphone,
  check: Check,
  close: Close,
  camera: Camera,
  inbox: Inbox,
  external: ExternalLink,
  wifi: Wifi,
  share: Share,
  'arrow-left': ArrowLeft,
  'chevron-left': ChevronLeft,
  'chevron-right': ChevronRight,
  lightbulb: Lightbulb,
  list: List,
  'more-horizontal': MoreHorizontal,
  'more-vertical': MoreVertical,
  'map-pin': MapPin,
  upload: Upload,
  filter: Filter,
  play: Play,
  square: Square,
  plus: Plus,
  'alert-circle': AlertCircle,
  'chevron-down': ChevronDown,
  'user-plus': UserPlus,
  'edit-3': Edit3,
  'log-in': LogIn,
  'message-circle': MessageCircle,
  'align-left': AlignLeft,
  'align-center': AlignCenter,
  'align-right': AlignRight,
}

export function getIcon(name) {
  return ICON_MAP[name] || Bookmark
}
