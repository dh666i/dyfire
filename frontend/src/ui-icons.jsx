import React from 'react';

export function StatusIcon({kind='success',className=''}) {
  const paths={
    close:<path d="m6 6 12 12M6 18 18 6"/>,
    chevron:<path d="m7 10 5 5 5-5"/>,
    chevronRight:<path d="m9 6 6 6-6 6"/>,
    chevronLeft:<path d="m15 6-6 6 6 6"/>,
    externalLink:<><path d="M14 3h7v7m0-7L10 14"/><path d="M10 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-5"/></>,
    key:<><circle cx="8" cy="9" r="4"/><path d="m11 12 9 9m-5-5 2-2m0 4 2-2"/></>,
    mail:<><rect x="3" y="5" width="18" height="14" rx="3"/><path d="m4 7 8 6 8-6"/></>,
    more:<g fill="currentColor" stroke="none"><circle cx="12" cy="5" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="12" cy="19" r="1.8"/></g>,
    loading:<><circle cx="12" cy="12" r="8" opacity=".22"/><path d="M12 4a8 8 0 0 1 8 8"/></>,
    success:<path d="m5 12 4 4L19 6"/>,
    error:<><circle cx="12" cy="12" r="9"/><path d="m9 9 6 6m0-6-6 6"/></>,
    warning:<><path d="m10.3 4.3-8 14A1.8 1.8 0 0 0 4 21h16a1.8 1.8 0 0 0 1.7-2.7l-8-14a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4m0 4h.01"/></>,
    info:<><circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10h.01"/></>,
  };
  return <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[kind]||paths.success}</svg>;
}
