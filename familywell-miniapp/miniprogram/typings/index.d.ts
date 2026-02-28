interface IAppOption {
  globalData: {
    token: string
    userInfo: any
    chatInitQuestion: string
  }
  setToken(token: string, userInfo: any): void
  clearToken(): void
  isLoggedIn(): boolean
}
