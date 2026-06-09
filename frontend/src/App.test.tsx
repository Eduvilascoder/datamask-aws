import { render } from '@testing-library/react';
import { describe, it, expect } from 'vitest';

describe('Vitest setup', () => {
  it('renders a basic React component', () => {
    const { container } = render(<div data-testid="hello">Hello DataMask</div>);
    expect(container.textContent).toBe('Hello DataMask');
  });

  it('jest-dom matchers work', () => {
    const { getByTestId } = render(
      <button data-testid="btn" disabled>
        Click
      </button>,
    );
    expect(getByTestId('btn')).toBeDisabled();
  });
});
