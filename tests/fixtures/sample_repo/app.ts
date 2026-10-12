import { formatResult, PrintOptions } from "./formatter";

export function runCalculationApp(): string {
  const opts: PrintOptions = { uppercase: true, prefix: "ApexCalc: " };
  return formatResult(42, opts);
}
