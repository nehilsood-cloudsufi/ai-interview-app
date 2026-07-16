import { useState, type ChangeEvent } from 'react';

export function useResumeFiles(onError: (message: string | null) => void) {
  const [files, setFiles] = useState<File[]>([]);

  const handleFileChange = (e: ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) {
      const newFiles = Array.from(e.target.files);
      if (files.length + newFiles.length > 5) {
        onError("You can only upload up to 5 files.");
        return;
      }

      for (const file of newFiles) {
        if (file.size > 5 * 1024 * 1024) {
          onError(`File ${file.name} is larger than 5MB.`);
          return;
        }
        const ext = file.name.split('.').pop()?.toLowerCase();
        if (!['pdf', 'docx', 'txt'].includes(ext || '')) {
          onError(`File ${file.name} is not supported. Use PDF, DOCX, or TXT.`);
          return;
        }
      }

      onError(null);
      setFiles(prev => [...prev, ...newFiles]);
    }
  };

  const removeFile = (index: number) => {
    setFiles(prev => prev.filter((_, i) => i !== index));
  };

  return { files, handleFileChange, removeFile };
}
