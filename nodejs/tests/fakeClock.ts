/**
 * A clock the suite moves by hand, in place of the SDK's own.
 *
 * Renewal is decided against two clocks — a monotonic one for when, and the
 * wall clock for reading a key's absolute expiry — so both are replaced
 * together and moved together, as real time moves them.
 */

import { clock } from '../src/internal/auth.js'

/** Both of the SDK's clocks, standing still until {@link FakeClock.advance}. */
export class FakeClock {
  /** Milliseconds on the wall clock, starting on a whole second. */
  private wall = Math.floor(Date.now() / 1000) * 1000

  /** Milliseconds on the monotonic clock. */
  private elapsed = 1_000_000

  private readonly saved = { ...clock }

  /** Take the SDK's clocks over. Undo it with {@link FakeClock.restore}. */
  install(): this {
    clock.monotonic = () => this.elapsed
    clock.now = () => this.wall
    return this
  }

  /** Give the SDK its own clocks back. */
  restore(): void {
    Object.assign(clock, this.saved)
  }

  /** The wall clock this fake reads, in milliseconds, for the fake service. */
  now = (): number => this.wall

  /**
   * Move both clocks forward.
   *
   * @param seconds How far, in seconds.
   */
  advance(seconds: number): void {
    this.wall += seconds * 1000
    this.elapsed += seconds * 1000
  }
}
