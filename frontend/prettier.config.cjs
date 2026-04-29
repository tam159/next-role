/**
 * @see https://prettier.io/docs/configuration
 * @type {import("prettier").Config}
 */
const config = {
  semi: true,
  trailingComma: "es5",
  singleQuote: false,
  printWidth: 100,
  tabWidth: 2,
  useTabs: false,
  arrowParens: "always",
  endOfLine: "lf",
  plugins: ["prettier-plugin-tailwindcss"],
};

module.exports = config;
