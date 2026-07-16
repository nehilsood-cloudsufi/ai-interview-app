import { X } from 'lucide-react';

interface ErrorToastProps {
  error: string | null;
  onDismiss: () => void;
}

export function ErrorToast({ error, onDismiss }: ErrorToastProps) {
  if (!error) return null;
  return (
      <div className="absolute top-4 left-1/2 -translate-x-1/2 bg-rose-500/90 backdrop-blur-md text-white px-6 py-3 rounded-xl shadow-2xl border border-rose-400/50 z-50 flex items-center gap-3">
          <X className="w-5 h-5" onClick={onDismiss} />
          <span className="font-medium">{error}</span>
      </div>
  );
}
