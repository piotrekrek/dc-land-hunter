import "@testing-library/jest-dom/vitest";

// jsdom has no layout engine and does not implement scrollIntoView; components call it to bring a row into view.
Element.prototype.scrollIntoView ??= () => {};
