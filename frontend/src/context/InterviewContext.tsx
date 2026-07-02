import React, { createContext, useContext, useState, ReactNode } from 'react';

type InterviewStage = 'greeting' | 'background' | 'technical' | 'behavioral' | 'closing' | 'completed';

interface InterviewContextType {
  participantName: string;
  setParticipantName: (name: string) => void;
  roomName: string;
  setRoomName: (room: string) => void;
  token: string | null;
  setToken: (token: string | null) => void;
  stage: InterviewStage;
  setStage: (stage: InterviewStage) => void;
}

const InterviewContext = createContext<InterviewContextType | undefined>(undefined);

export const InterviewProvider = ({ children }: { children: ReactNode }) => {
  const [participantName, setParticipantName] = useState('');
  const [roomName, setRoomName] = useState('');
  const [token, setToken] = useState<string | null>(null);
  const [stage, setStage] = useState<InterviewStage>('greeting');

  return (
    <InterviewContext.Provider
      value={{
        participantName,
        setParticipantName,
        roomName,
        setRoomName,
        token,
        setToken,
        stage,
        setStage,
      }}
    >
      {children}
    </InterviewContext.Provider>
  );
};

export const useInterview = () => {
  const context = useContext(InterviewContext);
  if (context === undefined) {
    throw new Error('useInterview must be used within an InterviewProvider');
  }
  return context;
};
