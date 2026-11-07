// vitest 5 no longer picks up the jest-dom matcher types through the `jest`
// namespace that `import "@testing-library/jest-dom"` augments, so pull in the
// vitest-specific augmentation (`declare module "vitest" { interface Assertion }`).
import "@testing-library/jest-dom/vitest";
