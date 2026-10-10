/** Design tokens consumed by the app provider and by messages rendered outside its setup. */
export const naiveThemeOverrides = {
  common: {
    primaryColor: '#2563eb',
    primaryColorHover: '#1d4ed8',
    primaryColorPressed: '#1e40af',
    primaryColorSuppl: '#2563eb',
    infoColor: '#2563eb',
    infoColorHover: '#1d4ed8',
    infoColorPressed: '#1e40af',
    borderRadius: '12px',
  },
  Input: {
    caretColor: '#2563eb',
    borderHover: '#93c5fd',
    borderFocus: '#2563eb',
    boxShadowFocus: '0 0 0 2px rgba(37, 99, 235, 0.15)',
  },
  Select: {
    peers: {
      InternalSelection: {
        borderHover: '#93c5fd',
        borderFocus: '#2563eb',
        boxShadowFocus: '0 0 0 2px rgba(37, 99, 235, 0.15)',
      },
    },
  },
  Switch: {
    railColorActive: '#2563eb',
    railColorActiveHover: '#1d4ed8',
    buttonColor: '#ffffff',
    boxShadowFocus: '0 0 0 2px rgba(37, 99, 235, 0.18)',
  },
}
