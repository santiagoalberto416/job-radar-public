import { useEffect, useRef } from "react";

/** Monospace output box that stays scrolled to the newest line. */
export default function Console({ text }: { text: string }) {
  const ref = useRef<HTMLPreElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [text]);
  return <pre ref={ref} className="console">{text}</pre>;
}
