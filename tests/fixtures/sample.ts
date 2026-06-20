export interface User {
  id: string;
  email: string;
}

export function makeUser(id: string, email: string): User {
  return { id, email };
}

export class UserService {
  private users: Map<string, User> = new Map();

  add(user: User): void {
    this.users.set(user.id, user);
  }

  get(id: string): User | undefined {
    return this.users.get(id);
  }
}
