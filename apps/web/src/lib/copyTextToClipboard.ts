interface ClipboardWriter {
  writeText(text: string): Promise<void>
}

interface ClipboardEnvironment {
  clipboard?: ClipboardWriter
  document?: Document
  secureContext?: boolean
}

function browserEnvironment(): ClipboardEnvironment {
  return {
    clipboard: typeof navigator === 'undefined' ? undefined : navigator.clipboard,
    document: typeof document === 'undefined' ? undefined : document,
    secureContext: typeof window === 'undefined' ? undefined : window.isSecureContext,
  }
}

function copyWithSelection(text: string, documentRef: Document): boolean {
  if (!documentRef.body || typeof documentRef.execCommand !== 'function') return false

  const activeElement = documentRef.activeElement as HTMLElement | null
  const textarea = documentRef.createElement('textarea')
  textarea.value = text
  textarea.readOnly = true
  textarea.setAttribute('aria-hidden', 'true')
  Object.assign(textarea.style, {
    position: 'fixed',
    inset: '0 auto auto -9999px',
    opacity: '0',
    pointerEvents: 'none',
  })
  documentRef.body.appendChild(textarea)

  try {
    textarea.focus()
    textarea.select()
    textarea.setSelectionRange(0, textarea.value.length)
    return documentRef.execCommand('copy')
  } finally {
    textarea.remove()
    activeElement?.focus?.()
  }
}

/** Writes text in secure browsers and desktop WebViews, falling back to a selection copy. */
export async function copyTextToClipboard(
  text: string,
  environment: ClipboardEnvironment = browserEnvironment(),
): Promise<void> {
  if (environment.clipboard?.writeText) {
    try {
      await environment.clipboard.writeText(text)
      return
    } catch {
      // A desktop WebView may expose the Clipboard API yet still refuse writes.
    }
  }

  // An HTTP IP address does not count as a secure context. Chrome can report
  // success from the legacy copy command while never touching the system
  // clipboard.
  if (environment.secureContext === false) throw new Error('Clipboard writing is not allowed in an insecure context')

  if (environment.document && copyWithSelection(text, environment.document)) return
  throw new Error('The clipboard is unavailable in this environment')
}

export type { ClipboardEnvironment }
