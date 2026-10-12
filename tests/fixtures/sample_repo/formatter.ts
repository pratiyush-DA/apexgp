/**
 * Result formatting options.
 */
export interface PrintOptions {
  uppercase: boolean;
  prefix?: string;
}

/**
 * Formats a numeric output for console display.
 */
export function formatResult(value: number, options?: PrintOptions): string {
  const prefix = options?.prefix ?? "Result: ";
  const str = `${prefix}${value}`;
  return options?.uppercase ? str.toUpperCase() : str;
}
