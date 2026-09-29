export {};

declare global {
  interface Window {
    WebApp?: {
      initData?: string;
      platform?: string;
      version?: string;
      BackButton?: {
        show: () => void;
        hide: () => void;
        onClick: (callback: () => void) => void;
        offClick: (callback: () => void) => void;
      };
      openLink?: (url: string) => void;
      openMaxLink?: (url: string) => void;
      getViewportSize?: () => Promise<{ height: string; width: string }> | { height: string; width: string };
      enableClosingConfirmation?: () => void;
      disableClosingConfirmation?: () => void;
    };
  }
}
