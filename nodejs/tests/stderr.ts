/**
 * What the SDK writes to stderr while a piece of work runs.
 *
 * Every SDK record goes to `process.stderr`, looked up as each record is
 * written, so replacing `write` for the duration is enough to collect them.
 */

/**
 * Collect everything written to stderr while `body` runs.
 *
 * @param body The work to run, which may finish on a later turn of the loop.
 * @returns What was written, joined.
 */
export async function stderrOf(body: () => Promise<void>): Promise<string> {
  const chunks: string[] = []
  const original = process.stderr.write
  process.stderr.write = ((chunk: string | Uint8Array): boolean => {
    chunks.push(
      typeof chunk === 'string' ? chunk : Buffer.from(chunk).toString('utf8')
    )
    return true
  }) as typeof process.stderr.write
  try {
    await body()
  } finally {
    process.stderr.write = original
  }
  return chunks.join('')
}
