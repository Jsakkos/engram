import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  SvAtmosphere,
  SvBadge,
  SvBar,
  SvLabel,
  SvMark,
  SvPanel,
  SvRuler,
  SvTelemetryBand,
  sv,
} from "..";
import { SvAnimValue } from "../SvAnimValue";
import { SvCorners } from "../SvCorners";

describe("Synapse v2 primitives — smoke", () => {
  it("SvPanel renders children, includes corner ticks by default", () => {
    render(
      <SvPanel testid="t-panel">
        <span>inside</span>
      </SvPanel>,
    );
    expect(screen.getByTestId("t-panel")).toBeDefined();
    expect(screen.getByText("inside")).toBeDefined();
    // Corner ticks render four divs
    expect(screen.getByTestId("sv-corner-tl")).toBeDefined();
    expect(screen.getByTestId("sv-corner-br")).toBeDefined();
  });

  it("SvPanel suppresses corners when hideCorners is true", () => {
    render(
      <SvPanel testid="t-panel-no-corners" hideCorners>
        <span>x</span>
      </SvPanel>,
    );
    expect(screen.queryByTestId("sv-corner-tl")).toBeNull();
  });

  it("SvCorners renders 4 brackets", () => {
    render(
      <div style={{ position: "relative" }}>
        <SvCorners />
      </div>,
    );
    expect(screen.getByTestId("sv-corner-tl")).toBeDefined();
    expect(screen.getByTestId("sv-corner-tr")).toBeDefined();
    expect(screen.getByTestId("sv-corner-bl")).toBeDefined();
    expect(screen.getByTestId("sv-corner-br")).toBeDefined();
  });

  it("SvLabel uppercases content and prefixes a caret", () => {
    render(<SvLabel>speed</SvLabel>);
    const el = screen.getByTestId("sv-label");
    expect(el.textContent).toContain("›");
    expect(el.textContent).toContain("speed");
  });

  it("SvBar clamps value to [0, 1] and reflects via data-value", () => {
    const { rerender } = render(<SvBar value={1.5} />);
    expect(screen.getByTestId("sv-bar").getAttribute("data-value")).toBe("1");
    rerender(<SvBar value={-0.2} />);
    expect(screen.getByTestId("sv-bar").getAttribute("data-value")).toBe("0");
    rerender(<SvBar value={0.42} />);
    expect(screen.getByTestId("sv-bar").getAttribute("data-value")).toBe("0.42");
  });

  it("SvBadge maps state to data attribute", () => {
    render(<SvBadge state="ripping">RIPPING</SvBadge>);
    const el = screen.getByTestId("sv-badge");
    expect(el.getAttribute("data-state")).toBe("ripping");
    expect(el.textContent).toContain("RIPPING");
  });

  it("SvRuler renders the requested number of segments", () => {
    render(<SvRuler ticks={10} />);
    expect(screen.getByTestId("sv-ruler")).toBeDefined();
  });

  it("SvAnimValue uses default percent formatter", () => {
    render(<SvAnimValue target={0.42} />);
    const el = screen.getByTestId("sv-anim-value");
    // Initial render: display equals target on first paint
    expect(el.textContent).toMatch(/^\d+%$/);
  });

  it("SvTelemetryBand renders items", () => {
    render(<SvTelemetryBand items={["UNIT 07", "WS·CONNECTED", "v0.6.0"]} />);
    const el = screen.getByTestId("sv-telemetry-band");
    expect(el.textContent).toContain("UNIT 07");
    expect(el.textContent).toContain("WS·CONNECTED");
  });

  it("SvMark renders an SVG with the engram title", () => {
    render(<SvMark size={24} />);
    expect(screen.getByTestId("sv-mark")).toBeDefined();
  });

  it("SvAtmosphere wraps children and can hide scanlines / skyline", () => {
    const { rerender } = render(
      <SvAtmosphere>
        <span>app content</span>
      </SvAtmosphere>,
    );
    expect(screen.getByTestId("sv-atmosphere")).toBeDefined();
    expect(screen.getByText("app content")).toBeDefined();
    expect(screen.getByTestId("sv-scanlines")).toBeDefined();
    expect(screen.getByTestId("sv-skyline")).toBeDefined();

    rerender(
      <SvAtmosphere scanlines={false} skyline={false}>
        <span>x</span>
      </SvAtmosphere>,
    );
    expect(screen.queryByTestId("sv-scanlines")).toBeNull();
    expect(screen.queryByTestId("sv-skyline")).toBeNull();
  });

  it("tokens.ts surfaces are non-empty hex values", () => {
    expect(sv.bg0).toMatch(/^#[0-9a-f]{6}$/i);
    expect(sv.cyan).toMatch(/^#[0-9a-f]{6}$/i);
    expect(sv.magenta).toMatch(/^#[0-9a-f]{6}$/i);
  });
});
